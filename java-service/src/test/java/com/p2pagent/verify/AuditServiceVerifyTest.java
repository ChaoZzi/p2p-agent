package com.p2pagent.verify;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.p2pagent.api.dto.AuditView;
import com.p2pagent.db.AuditRepository;
import com.p2pagent.engine.AuditAction;
import com.p2pagent.engine.AuditService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.env.Environment;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.lang.reflect.Method;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 【独立验证】AuditService —— 由 Hermes 编写，不是实现者自测。
 *
 * <p><b>真源</b>：docs/task-cards/P0-02-你敲规格-Java.md §4（AuditService 规格与四条硬规矩）
 * + §0.5 的审计流水表（一次"建单+同意"应留下 5 行、每行 7 个字段的语义）。
 *
 * <p>覆盖：append 真的落库（直查 SQL 不信服务自己读回来的）、auditId 唯一性、at 的 ISO-8601 形状、
 * traceId 的传递（有 MDC 时用 MDC，无请求上下文时兜底 "unknown"）、listByFlow 的升序与隔离性、
 * AuditView 的对外 JSON 契约（字段名一个都不能变）、AuditAction 枚举取值集合、
 * append-only 的结构性保障（仓储层没有任何 update/delete 方法）、
 * 以及（仅 postgres profile）数据库层的 REVOKE 是否真的生效。
 */
@SpringBootTest
class AuditServiceVerifyTest {

    @Autowired
    private AuditService auditService;          // 被测对象

    @Autowired
    private JdbcTemplate jdbcTemplate;          // 用来"绕过服务"直查库、预置受控数据

    @Autowired
    private ObjectMapper objectMapper;          // 用来验 DTO 的对外 JSON 契约

    @Autowired
    private Environment environment;            // 用来判断当前跑在哪个 profile 上

    /** 每个测试用独立的 flowId，互不干扰；结束后清理（PG 上会清不掉，见 cleanup）。 */
    private final List<String> createdFlowIds = new ArrayList<>();

    private String newFlowId() {
        String flowId = "verify-flow-" + UUID.randomUUID();
        createdFlowIds.add(flowId);
        return flowId;
    }

    @AfterEach
    void cleanup() {
        for (String flowId : createdFlowIds) {
            try {
                jdbcTemplate.update("DELETE FROM audit_log WHERE flow_id = ?", flowId);
            } catch (DataAccessException e) {
                // postgres profile 上 audit_log 被 REVOKE 了 DELETE —— 这正是规格要的"数据库层保障"，
                // 所以这里容忍失败（测试数据留在库里无害）。
            }
        }
        createdFlowIds.clear();
        MDC.remove("traceId");
    }

    // ---------------------------------------------------------------- append

    @Test
    @DisplayName("append 之后：这一行真的进了库，七个字段都对得上")
    void append_persists_the_row_with_all_fields() {
        String flowId = newFlowId();
        String atBefore = Instant.now().toString();

        auditService.append(flowId, "zhangsan", AuditAction.APPROVED, "{\"reason\":\"ok\"}");

        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT audit_id, flow_id, actor, action, detail, trace_id, at FROM audit_log WHERE flow_id = ?", flowId);
        assertEquals(1, rows.size(), "append 一次必须在库里留下一行");

        Map<String, Object> row = rows.get(0);
        assertEquals(flowId, row.get("flow_id"), "flow_id 必须是调用方传进来的那张单");
        assertEquals("zhangsan", row.get("actor"), "actor 必须原样落库（追责链靠它）");
        assertEquals(AuditAction.APPROVED.name(), row.get("action"), "action 落库的应是枚举名");
        assertEquals("{\"reason\":\"ok\"}", row.get("detail"), "detail 必须原样落库");

        String auditId = (String) row.get("audit_id");
        assertNotNull(auditId, "audit_id 不能为空");
        assertFalse(auditId.isBlank(), "audit_id 不能是空白串");
        assertEquals(auditId, auditId.trim(), "audit_id 不应带首尾空格");

        String at = (String) row.get("at");
        assertNotNull(at, "at 不能为空");
        Instant parsed = Instant.parse(at);   // 不是 ISO-8601 这里就抛异常 —— 契约要求可解析
        assertFalse(parsed.isBefore(Instant.parse(atBefore).minusSeconds(5)),
                "at 应接近写入时刻，实际: " + at);
    }

    @Test
    @DisplayName("auditId 每次都不一样（同一次请求里写多行也不能撞）")
    void audit_id_is_unique_per_row() {
        String flowId = newFlowId();
        auditService.append(flowId, "system", AuditAction.FLOW_CREATED, "{}");
        auditService.append(flowId, "system", AuditAction.SUBMITTED, "{}");
        auditService.append(flowId, "system", AuditAction.APPROVAL_REQUESTED, "{}");

        List<String> ids = jdbcTemplate.queryForList(
                "SELECT audit_id FROM audit_log WHERE flow_id = ?", String.class, flowId);
        assertEquals(3, ids.size(), "三次 append 应有三行");
        assertEquals(3, new HashSet<>(ids).size(), "audit_id 必须三行三个不同的值");
    }

    @Test
    @DisplayName("traceId 传递：有 MDC 时用它，没有请求上下文时兜底 unknown")
    void trace_id_comes_from_current_request() {
        String flowId = newFlowId();
        MDC.put("traceId", "tr-verify-abc123");
        try {
            auditService.append(flowId, "system", AuditAction.COMPLETED, "{}");
        } finally {
            MDC.remove("traceId");
        }
        String traced = jdbcTemplate.queryForObject(
                "SELECT trace_id FROM audit_log WHERE flow_id = ?", String.class, flowId);
        assertEquals("tr-verify-abc123", traced, "审计行必须带上当前请求的 trace_id（否则两端对不上账）");

        String flowId2 = newFlowId();
        auditService.append(flowId2, "system", AuditAction.COMPLETED, "{}");
        String untraced = jdbcTemplate.queryForObject(
                "SELECT trace_id FROM audit_log WHERE flow_id = ?", String.class, flowId2);
        assertEquals("unknown", untraced, "无请求上下文时 TraceFilter 兜底为 unknown（而不是 null）");
    }

    // ------------------------------------------------------------- listByFlow

    @Test
    @DisplayName("listByFlow 按 at 升序（用乱序预置的数据验证，不靠等待时间）")
    void listByFlow_is_ordered_by_at_ascending() {
        String flowId = newFlowId();
        // 直接写库、故意乱序：这样排序职责就只能由查询语句承担
        insertRaw(flowId, "2026-01-01T00:00:03Z", "third");
        insertRaw(flowId, "2026-01-01T00:00:01Z", "first");
        insertRaw(flowId, "2026-01-01T00:00:02Z", "second");

        List<AuditView> rows = auditService.listByFlow(flowId);

        assertEquals(3, rows.size(), "应返回该单的全部 3 行");
        assertEquals(List.of("2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", "2026-01-01T00:00:03Z"),
                rows.stream().map(AuditView::at).toList(), "必须按 at 升序返回");
        assertEquals(List.of("first", "second", "third"),
                rows.stream().map(AuditView::detail).toList(), "顺序错了会把流水读反");
    }

    @Test
    @DisplayName("listByFlow 只返回指定单的流水（不会串到别的单）")
    void listByFlow_is_isolated_per_flow() {
        String flowA = newFlowId();
        String flowB = newFlowId();
        auditService.append(flowA, "system", AuditAction.FLOW_CREATED, "{\"who\":\"A\"}");
        auditService.append(flowB, "system", AuditAction.FLOW_CREATED, "{\"who\":\"B\"}");

        List<AuditView> rowsOfA = auditService.listByFlow(flowA);
        assertEquals(1, rowsOfA.size(), "A 单只应看到自己那 1 行");
        assertEquals(flowA, rowsOfA.get(0).flowId(), "返回行的 flow_id 必须是 A");
        assertTrue(rowsOfA.get(0).detail().contains("A"), "读到的应是 A 单的内容");
    }

    @Test
    @DisplayName("没有流水的单：返回空列表而不是 null")
    void listByFlow_returns_empty_list_when_no_rows() {
        List<AuditView> rows = auditService.listByFlow("verify-flow-no-such-" + UUID.randomUUID());
        assertNotNull(rows, "没有流水时应返回空列表，不能是 null");
        assertTrue(rows.isEmpty(), "没有流水时应返回空列表");
    }

    // ------------------------------------------------------- 契约与结构性保障

    @Test
    @DisplayName("对外 JSON 契约：字段名必须逐字是 snake_case 那七个")
    void audit_view_json_contract_is_locked() throws Exception {
        AuditView view = new AuditView("a1", "f1", "zhangsan", "APPROVED", "{}", "tr-1", "2026-10-03T00:00:00Z");
        Map<?, ?> json = objectMapper.readValue(objectMapper.writeValueAsString(view), Map.class);
        assertEquals(
                Set.of("audit_id", "flow_id", "actor", "action", "detail", "trace_id", "at"),
                json.keySet(),
                "对外字段名是跨语言契约（Python 侧按它取值），改名会静默破坏契约");
    }

    @Test
    @DisplayName("AuditAction 的取值集合与规格一致（枚举名是契约，不能被随手改名）")
    void audit_action_names_match_spec() {
        Set<String> actual = Arrays.stream(AuditAction.values()).map(Enum::name).collect(java.util.stream.Collectors.toSet());
        assertEquals(Set.of("FLOW_CREATED", "SUBMITTED", "APPROVAL_REQUESTED", "APPROVED",
                "REJECTED", "COMPLETED", "REVISE_REQUESTED"), actual);
    }

    @Test
    @DisplayName("append-only 的结构性保障：仓储层不存在任何 update/delete 方法")
    void audit_repository_has_no_mutating_methods() {
        for (Method m : AuditRepository.class.getDeclaredMethods()) {
            String name = m.getName().toLowerCase();
            assertFalse(name.contains("update") || name.contains("delete") || name.contains("remove")
                            || name.contains("modify") || name.contains("set"),
                    "审计仓储不允许出现能改/删已有流水的入口，发现: " + m.getName());
        }
    }

    @Test
    @DisplayName("数据库层保障（仅 postgres profile）：UPDATE/DELETE audit_log 必须被数据库拒绝")
    void postgres_revoke_blocks_updates() {
        boolean onPostgres = Arrays.asList(environment.getActiveProfiles()).contains("postgres");
        Assumptions.assumeTrue(onPostgres, "sqlite 没有权限模型，这层保障只在 postgres profile 上成立");

        String flowId = newFlowId();
        auditService.append(flowId, "system", AuditAction.FLOW_CREATED, "{}");
        assertThrows(DataAccessException.class,
                () -> jdbcTemplate.update("UPDATE audit_log SET actor = 'hacker' WHERE flow_id = ?", flowId),
                "postgres 上应因 REVOKE 而拒绝 UPDATE —— 这是 append-only 的第二层保障");
    }

    // ---------------------------------------------------------------- helpers

    /** 绕过服务、直接写库（用受控的 at 值造数据，用于验证排序职责落在查询语句上）。 */
    private void insertRaw(String flowId, String at, String detail) {
        jdbcTemplate.update(
                "INSERT INTO audit_log(audit_id, flow_id, actor, action, detail, trace_id, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID().toString(), flowId, "system", "FLOW_CREATED", detail, "tr-seed", at);
    }
}
