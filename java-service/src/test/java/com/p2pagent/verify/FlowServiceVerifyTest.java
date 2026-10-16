package com.p2pagent.verify;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.p2pagent.api.dto.CreateFlowCommand;
import com.p2pagent.api.dto.FlowView;
import com.p2pagent.db.FlowRepository;
import com.p2pagent.engine.AuditAction;
import com.p2pagent.engine.AuditService;
import com.p2pagent.engine.FlowService;
import com.p2pagent.engine.FlowState;
import com.p2pagent.mockoa.error.ApiException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.lang.reflect.Method;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 【独立验证】FlowService —— 由 Hermes 编写，不是实现者自测。
 *
 * <p><b>真源</b>：手抄自 {@code docs/task-cards/P0-02-你敲规格-Java.md} §3 方法 1~4 的
 * 「跑完的结果」与「三条纪律」，以及 §0.5 的审计流水表。期望值写在下面的
 * {@code SPEC_*} 常量里，<b>不读实现反推</b> —— 否则实现错了测试跟着错。
 *
 * <p>覆盖四类：
 * <ol>
 *   <li><b>行为</b>：建单落 <code>PENDING_APPROVAL</code> + 3 条审计；approve 两跳 + 共 5 条；
 *       reject 1 跳 + 共 4 条；幂等同 request_id 返回原单且审计不重复；并发 5 线程只产 1 张单。</li>
 *   <li><b>落库真相</b>：全部断言走 {@code JdbcTemplate} 直查 SQL，不信服务自己的读路径。</li>
 *   <li><b>契约</b>：<code>state / action</code> 落库的是枚举名；<code>detail</code> 是 JSON 串且键集合固定；
 *       时间字段是 ISO-8601；同一次请求的审计行 {@code trace_id} 相同。</li>
 *   <li><b>结构</b>：三个写方法带 {@code @Transactional(REQUIRED)}，而被调的 {@code AuditService.append}
 *       不自开事务（写 {@code REQUIRES_NEW} 会出现"父事务回滚了、审计还在"）。</li>
 * </ol>
 *
 * <p>断言消息一律英文（Windows 控制台是 GBK，中文会打成乱码并被 grep 当二进制）。
 */
@SpringBootTest
class FlowServiceVerifyTest {

    // ── 真源：规格 §3「跑完的结果」手抄（顺序不参与断言，只锁"是哪几条"）─────────
    private static final Set<String> SPEC_CREATE_ACTIONS =
            Set.of("FLOW_CREATED", "SUBMITTED", "APPROVAL_REQUESTED");
    private static final Set<String> SPEC_APPROVE_ACTIONS =
            Set.of("FLOW_CREATED", "SUBMITTED", "APPROVAL_REQUESTED", "APPROVED", "COMPLETED");
    private static final Set<String> SPEC_REJECT_ACTIONS =
            Set.of("FLOW_CREATED", "SUBMITTED", "APPROVAL_REQUESTED", "REJECTED");

    /** 规格 §0.5：系统自动动作的 actor。 */
    private static final String SPEC_SYSTEM_ACTOR = "system";

    private static final String REQUEST_ID_PREFIX = "verify-req-";

    @Autowired
    private FlowService flowService;        // 被测对象

    @Autowired
    private JdbcTemplate jdbcTemplate;      // 直查库、绕过服务读路径

    @Autowired
    private ObjectMapper objectMapper;      // 验 JSON 契约

    @Autowired
    private FlowRepository flowRepository;  // 只用来做"仓储层的独立事实"对照

    private String newRequestId() {
        return REQUEST_ID_PREFIX + UUID.randomUUID();
    }

    private static CreateFlowCommand cmd() {
        return new CreateFlowCommand("laptop", 2, "team needs replacements", "CC-VERIFY");
    }

    /**
     * 清理本类造的数据。postgres profile 上 audit_log 被 REVOKE 了 DELETE —— 那正是规格要的
     * "数据库层保障"，所以这里容忍失败（开发库里会残留 verify-req-* 行，属预期）。
     */
    @AfterEach
    void cleanup() {
        deleteQuietly("DELETE FROM audit_log WHERE flow_id IN "
                + "(SELECT id FROM flow WHERE request_id LIKE '" + REQUEST_ID_PREFIX + "%')");
        deleteQuietly("DELETE FROM flow WHERE request_id LIKE '" + REQUEST_ID_PREFIX + "%'");
    }

    private void deleteQuietly(String sql) {
        try {
            jdbcTemplate.update(sql);
        } catch (DataAccessException e) {
            // postgres: audit_log 的 DELETE 被 REVOKE；sqlite: 不该走到这里
        }
    }

    // ─────────────────────────────────────────────────── 方法 1：createFlow

    @Test
    @DisplayName("建单：落 PENDING_APPROVAL，flow 恰 1 行，audit_log 恰 3 行（FLOW_CREATED/SUBMITTED/APPROVAL_REQUESTED）")
    void createFlow_lands_on_pending_approval_with_three_audit_rows() {
        String requestId = newRequestId();

        FlowView view = flowService.createFlow(requestId, cmd());

        // 1) 返回值：规格 §3 方法1「跑完的结果」——状态 PENDING_APPROVAL
        assertEquals("PENDING_APPROVAL", view.state(), "createFlow must end on PENDING_APPROVAL");
        assertEquals(requestId, view.requestId(), "returned view must carry the caller's request_id");
        assertNotNull(view.id(), "flow id must be generated");

        // 2) 落库真相：直查 flow 表
        List<Map<String, Object>> flowRows = jdbcTemplate.queryForList(
                "SELECT id, request_id, state, item, qty, reason, cost_center, created_at, updated_at "
                        + "FROM flow WHERE request_id = ?", requestId);
        assertEquals(1, flowRows.size(), "exactly one flow row per request_id");

        Map<String, Object> row = flowRows.get(0);
        assertEquals(view.id(), row.get("id"), "returned id must be the persisted id");
        assertEquals("PENDING_APPROVAL", row.get("state"), "state column must hold the enum NAME, not the ordinal");
        assertEquals("laptop", row.get("item"), "item must be persisted verbatim");
        assertEquals(2, ((Number) row.get("qty")).intValue(), "qty must be persisted");
        assertEquals("team needs replacements", row.get("reason"), "reason must be persisted verbatim");
        assertEquals("CC-VERIFY", row.get("cost_center"), "cost_center must be persisted verbatim");

        // 3) 时间字段是 ISO-8601 字符串，且 updated_at 不早于 created_at
        Instant createdAt = Instant.parse((String) row.get("created_at"));
        Instant updatedAt = Instant.parse((String) row.get("updated_at"));
        assertFalse(updatedAt.isBefore(createdAt), "updated_at must not precede created_at");

        // 4) 审计：条数 + 是哪三条 + 谁做的 + trace 一致
        List<Map<String, Object>> auditRows = auditRows(view.id());
        assertEquals(3, auditRows.size(), "createFlow must leave exactly 3 audit rows");

        Set<String> actions = new TreeSet<>();
        for (Map<String, Object> r : auditRows) {
            actions.add((String) r.get("action"));
        }
        assertEquals(new TreeSet<>(SPEC_CREATE_ACTIONS), actions, "createFlow audit actions mismatch (spec §3)");

        assertEquals(0, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ? AND actor <> ?",
                        view.id(), SPEC_SYSTEM_ACTOR),
                "all 3 create-time audit rows must have actor='system' (nobody human acted yet)");
        assertEquals(1, count("SELECT COUNT(DISTINCT trace_id) FROM audit_log WHERE flow_id = ?", view.id()),
                "the 3 rows of one request must share one trace_id (对账靠它)");
        assertNonDecreasingAt(view.id());
    }

    @Test
    @DisplayName("建单的 detail 契约：FLOW_CREATED 是 {item,qty}，另两条是 {}（都是合法 JSON）")
    void createFlow_audit_detail_follows_the_json_contract() throws Exception {
        String requestId = newRequestId();
        FlowView view = flowService.createFlow(requestId, cmd());

        String createdDetail = singleString(
                "SELECT detail FROM audit_log WHERE flow_id = ? AND action = ?", view.id(), "FLOW_CREATED");
        assertEquals(Set.of("item", "qty"), jsonKeys(createdDetail),
                "FLOW_CREATED.detail must be a JSON object with exactly {item, qty}");
        assertEquals("laptop", objectMapper.readTree(createdDetail).get("item").asText(),
                "FLOW_CREATED.detail.item must equal the submitted item");

        for (String action : List.of("SUBMITTED", "APPROVAL_REQUESTED")) {
            String detail = singleString(
                    "SELECT detail FROM audit_log WHERE flow_id = ? AND action = ?", view.id(), action);
            assertEquals(Set.of(), jsonKeys(detail), action + ".detail must be an empty JSON object");
        }
    }

    @Test
    @DisplayName("幂等：同 request_id 二次建单返回原单，flow 仍 1 行、审计仍 3 行（不重复写）")
    void createFlow_is_idempotent_per_request_id() {
        String requestId = newRequestId();

        FlowView first = flowService.createFlow(requestId, cmd());
        FlowView second = flowService.createFlow(requestId, cmd());

        assertEquals(first.id(), second.id(), "second call must return the ORIGINAL flow");
        assertEquals(first.createdAt(), second.createdAt(), "original created_at must be untouched");
        assertEquals("PENDING_APPROVAL", second.state(), "original flow must still be PENDING_APPROVAL");

        assertEquals(1, count("SELECT COUNT(*) FROM flow WHERE request_id = ?", requestId),
                "idempotent create must not insert a second flow row");
        assertEquals(3, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ?", first.id()),
                "a deduplicated request must not append audit rows");
    }

    @Test
    @DisplayName("并发同 request_id：5 个线程都拿到同一张单，库里只有 1 行 flow + 3 行审计")
    void createFlow_concurrent_same_request_id_yields_exactly_one_flow() throws Exception {
        String requestId = newRequestId();
        int threads = 5;
        CyclicBarrier barrier = new CyclicBarrier(threads);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        List<Future<FlowView>> futures = new ArrayList<>();
        try {
            for (int i = 0; i < threads; i++) {
                futures.add(pool.submit(() -> {
                    barrier.await(20, TimeUnit.SECONDS);
                    return flowService.createFlow(requestId, cmd());
                }));
            }

            Set<String> ids = new HashSet<>();
            List<String> errors = new ArrayList<>();
            for (Future<FlowView> f : futures) {
                try {
                    FlowView v = f.get(60, TimeUnit.SECONDS);
                    assertNotNull(v, "concurrent createFlow must never return null");
                    ids.add(v.id());
                } catch (ExecutionException e) {
                    errors.add(String.valueOf(e.getCause()));
                }
            }

            assertTrue(errors.isEmpty(), "concurrent createFlow must not throw, but got: " + errors);
            assertEquals(1, ids.size(), "all concurrent callers must get the SAME flow id, got: " + ids);
        } finally {
            pool.shutdownNow();
        }

        assertEquals(1, count("SELECT COUNT(*) FROM flow WHERE request_id = ?", requestId),
                "concurrent create must still leave exactly one flow row (ON CONFLICT DO NOTHING path)");
        assertEquals(3, count("SELECT COUNT(*) FROM audit_log WHERE flow_id IN "
                        + "(SELECT id FROM flow WHERE request_id = ?)", requestId),
                "the losing threads must NOT append audit rows");
    }

    @Test
    @DisplayName("幂等插入（FlowService 里 row==0 那条兜底路径的机制）：同 request_id 第二次返回 0，原单不被覆盖")
    void insertFlowIfAbsent_returns_zero_on_conflict_and_keeps_the_first_row() {
        String requestId = newRequestId();
        String firstId = "verify-" + UUID.randomUUID();
        String conflictingId = "verify-" + UUID.randomUUID();

        int first = flowRepository.insertFlowIfAbsent(
                new FlowView(firstId, requestId, FlowState.DRAFT.name(), "laptop", 2,
                        "first wins", "CC-VERIFY", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"));
        assertEquals(1, first, "first insert must report 1 affected row (it IS the winner)");

        int second = flowRepository.insertFlowIfAbsent(
                new FlowView(conflictingId, requestId, FlowState.DRAFT.name(), "monitor", 9,
                        "loser", "CC-OTHER", "2026-01-01T00:00:09Z", "2026-01-01T00:00:09Z"));
        assertEquals(0, second,
                "ON CONFLICT(request_id) DO NOTHING must report 0 rows — this is what FlowService reads as 'already exists'");

        assertEquals(1, count("SELECT COUNT(*) FROM flow WHERE request_id = ?", requestId),
                "the losing insert must not create a second row");
        assertEquals(firstId, singleString("SELECT id FROM flow WHERE request_id = ?", requestId),
                "the first row must survive: DO NOTHING never overwrites");
        assertEquals("first wins", singleString("SELECT reason FROM flow WHERE request_id = ?", requestId),
                "the loser's payload must not leak into the stored row");
    }

    // ─────────────────────────────────────────────────── 方法 3：approve

    @Test
    @DisplayName("approve：两跳推完到 COMPLETED，审计共 5 行，APPROVED/COMPLETED 的 actor 必须是传入的审批人")
    void approve_records_the_operator_as_actor_and_reaches_completed() {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());

        FlowView approved = flowService.approve(created.id(), "lisi");

        assertEquals("COMPLETED", approved.state(), "approve must run PENDING_APPROVAL->APPROVED->COMPLETED");

        List<Map<String, Object>> auditRows = auditRows(created.id());
        assertEquals(5, auditRows.size(), "create(3) + approve(2) must be 5 audit rows (spec §0.5)");
        Set<String> actions = new TreeSet<>();
        for (Map<String, Object> r : auditRows) {
            actions.add((String) r.get("action"));
        }
        assertEquals(new TreeSet<>(SPEC_APPROVE_ACTIONS), actions, "approve audit actions mismatch (spec §3)");

        // 最值钱的一条：审批人的名字不能丢成 "system"
        for (String action : List.of("APPROVED", "COMPLETED")) {
            assertEquals("lisi", singleString(
                            "SELECT actor FROM audit_log WHERE flow_id = ? AND action = ?", created.id(), action),
                    action + ".actor must be the operator passed in (X-Operator), not 'system'");
        }
        assertEquals(3, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ? AND actor = ?",
                        created.id(), SPEC_SYSTEM_ACTOR),
                "the 3 create-time rows must stay system; approve rows must not be system");

        assertEquals(1, count("SELECT COUNT(DISTINCT trace_id) FROM audit_log WHERE flow_id = ?", created.id()),
                "所有 5 行应共享同一个 trace_id");
        assertNonDecreasingAt(created.id());
    }

    // ─────────────────────────────────────────────────── 方法 4：reject

    @Test
    @DisplayName("reject：落 REJECTED，审计共 4 行，REJECTED 行的 actor=审批人、detail 是带 reason 的 JSON")
    void reject_records_reason_as_json_and_operator_as_actor() throws Exception {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());

        FlowView rejected = flowService.reject(created.id(), "wangwu", "budget frozen");

        assertEquals("REJECTED", rejected.state(), "reject must move PENDING_APPROVAL -> REJECTED");

        Set<String> actions = new TreeSet<>();
        for (Map<String, Object> r : auditRows(created.id())) {
            actions.add((String) r.get("action"));
        }
        assertEquals(new TreeSet<>(SPEC_REJECT_ACTIONS), actions, "reject audit actions mismatch (spec §3)");
        assertEquals(4, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ?", created.id()),
                "create(3) + reject(1) must be 4 audit rows (spec §0.5)");

        Map<String, Object> row = jdbcTemplate.queryForMap(
                "SELECT actor, detail FROM audit_log WHERE flow_id = ? AND action = ?", created.id(), "REJECTED");
        assertEquals("wangwu", row.get("actor"), "REJECTED.actor must be the operator passed in");

        String detail = (String) row.get("detail");
        assertEquals(Set.of("reason"), jsonKeys(detail), "REJECTED.detail must be a JSON object with exactly {reason}");
        assertEquals("budget frozen", objectMapper.readTree(detail).get("reason").asText(),
                "REJECTED.detail.reason must carry the reason verbatim");
    }

    // ─────────────────────────────────────────────────── 非法迁移 → 409

    @Test
    @DisplayName("已完成的单再 approve：409 FLOW_STATE_CONFLICT，状态与审计都不动")
    void approve_on_completed_flow_is_409_and_changes_nothing() {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());
        flowService.approve(created.id(), "lisi");

        ApiException ex = assertThrows(ApiException.class,
                () -> flowService.approve(created.id(), "lisi"),
                "approving a COMPLETED flow must be rejected by the state machine");

        assertEquals(409, ex.status().value(), "state conflict must be HTTP 409");
        assertEquals("FLOW_STATE_CONFLICT", ex.code(), "conflict must carry the contract error code");
        assertTrue(ex.getMessage().contains("COMPLETED -> APPROVED"),
                "409 message must name the real from->to, got: " + ex.getMessage());
        assertTrue(ex.getMessage().contains("allowed:"),
                "409 message must list allowed targets, got: " + ex.getMessage());

        assertEquals("COMPLETED", singleString("SELECT state FROM flow WHERE id = ?", created.id()),
                "a rejected transition must leave the state untouched");
        assertEquals(5, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ?", created.id()),
                "a rejected transition must not append audit rows");
    }

    @Test
    @DisplayName("已完成的单再 reject：409 FLOW_STATE_CONFLICT，状态与审计都不动")
    void reject_on_completed_flow_is_409_and_changes_nothing() {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());
        flowService.approve(created.id(), "lisi");

        ApiException ex = assertThrows(ApiException.class,
                () -> flowService.reject(created.id(), "wangwu", "too late"),
                "rejecting a COMPLETED flow must be rejected by the state machine");

        assertEquals(409, ex.status().value(), "state conflict must be HTTP 409");
        assertEquals("FLOW_STATE_CONFLICT", ex.code(), "conflict must carry the contract error code");

        assertEquals("COMPLETED", singleString("SELECT state FROM flow WHERE id = ?", created.id()),
                "a rejected transition must leave the state untouched");
        assertEquals(5, count("SELECT COUNT(*) FROM audit_log WHERE flow_id = ?", created.id()),
                "a rejected transition must not append audit rows");
    }

    // ─────────────────────────────────────────────────── 方法 2：getFlow

    @Test
    @DisplayName("getFlow：不存在的 id → 404 FLOW_NOT_FOUND，消息里带那个 id")
    void getFlow_unknown_id_is_404() {
        String ghost = "verify-ghost-" + UUID.randomUUID();

        ApiException ex = assertThrows(ApiException.class, () -> flowService.getFlow(ghost));
        assertEquals(404, ex.status().value(), "missing flow must be HTTP 404");
        assertEquals("FLOW_NOT_FOUND", ex.code(), "missing flow must carry the contract error code");
        assertTrue(ex.getMessage().contains(ghost), "404 message must name the id, got: " + ex.getMessage());
    }

    @Test
    @DisplayName("getFlow 与仓储层读的是同一张单（返回值来自库，不是内存想象）")
    void getFlow_returns_what_the_repository_holds() {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());

        FlowView read = flowService.getFlow(created.id());
        assertEquals(created.id(), read.id(), "getFlow must return the persisted flow");
        assertEquals("PENDING_APPROVAL", read.state(), "persisted state is the source of truth");
        assertEquals(read, flowRepository.findById(created.id()).orElseThrow(),
                "service 与仓储层读到的必须是同一行");
    }

    // ─────────────────────────────────────────────────── 结构层：事务纪律

    @Test
    @DisplayName("结构层：三个写方法带 @Transactional(REQUIRED)，getFlow 不带，AuditService.append 不自开事务")
    void write_methods_are_transactional_and_append_is_not() throws Exception {
        Method create = FlowService.class.getMethod("createFlow", String.class, CreateFlowCommand.class);
        Method approve = FlowService.class.getMethod("approve", String.class, String.class);
        Method reject = FlowService.class.getMethod("reject", String.class, String.class, String.class);

        for (Method m : List.of(create, approve, reject)) {
            Transactional tx = m.getAnnotation(Transactional.class);
            assertNotNull(tx, m.getName() + " must be @Transactional（状态变更与审计必须同事务，规格 §3 三条纪律）");
            assertEquals(Propagation.REQUIRED, tx.propagation(),
                    m.getName() + " must use REQUIRED so the write joins one transaction");
        }

        assertFalse(FlowService.class.getMethod("getFlow", String.class).isAnnotationPresent(Transactional.class),
                "getFlow is read-only; 规格 §3 明确它不需要事务");

        Method append = AuditService.class.getMethod(
                "append", String.class, String.class, AuditAction.class, String.class);
        Transactional appendTx = append.getAnnotation(Transactional.class);
        assertFalse(appendTx != null && appendTx.propagation() == Propagation.REQUIRES_NEW,
                "AuditService.append must NOT open its own transaction "
                        + "(REQUIRES_NEW 会造成「父事务回滚了、审计还在」)");
    }

    @Test
    @DisplayName("结构层：落库的 state 一律是 FlowState 枚举名（跨语言契约，不能是序号）")
    void persisted_state_is_the_enum_name() {
        String requestId = newRequestId();
        FlowView created = flowService.createFlow(requestId, cmd());

        Set<String> enumNames = new HashSet<>();
        for (FlowState s : FlowState.values()) {
            enumNames.add(s.name());
        }
        assertTrue(enumNames.contains(singleString("SELECT state FROM flow WHERE id = ?", created.id())),
                "flow.state must be one of FlowState names");
    }

    // ─────────────────────────────────────────────────── helpers

    private List<Map<String, Object>> auditRows(String flowId) {
        return jdbcTemplate.queryForList(
                "SELECT audit_id, flow_id, actor, action, detail, trace_id, at "
                        + "FROM audit_log WHERE flow_id = ? ORDER BY at ASC", flowId);
    }

    private int count(String sql, Object... args) {
        Integer n = jdbcTemplate.queryForObject(sql, Integer.class, args);
        return n == null ? -1 : n;
    }

    private String singleString(String sql, Object... args) {
        return jdbcTemplate.queryForObject(sql, String.class, args);
    }

    private Set<String> jsonKeys(String json) throws Exception {
        assertTrue(objectMapper.readTree(json).isObject(),
                "field must be a JSON object, got: " + json);
        Set<String> keys = new HashSet<>();
        objectMapper.readTree(json).fieldNames().forEachRemaining(keys::add);
        return keys;
    }

    /** at 是 ISO-8601 字符串，按字符串升序 == 按时间升序（同一 Instant 序列化格式一致）。 */
    private void assertNonDecreasingAt(String flowId) {
        List<String> ats = jdbcTemplate.queryForList(
                "SELECT at FROM audit_log WHERE flow_id = ? ORDER BY at ASC", String.class, flowId);
        Instant previous = null;
        for (String at : ats) {
            Instant current = Instant.parse(at);
            if (previous != null) {
                assertFalse(current.isBefore(previous), "audit rows must be non-decreasing in time");
            }
            previous = current;
        }
    }
}
