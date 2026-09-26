package com.p2pagent.db;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.p2pagent.api.dto.AuditView;
import com.p2pagent.api.dto.FlowView;
import java.lang.reflect.Method;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * 接口层的两道"地基"断言（P0-02 主体阶段 A 交付自带）。
 *
 * <p>为什么值得单独写测试：这两个行为是<b>别人（FlowService）要靠的</b>，靠嘴上说没用 ——
 * <ul>
 *   <li>{@code updateStateIfCurrent} 的返回值就是 409 FLOW_STATE_CONFLICT 的判据，
 *       它必须"只有第一个调用者拿到 1"；</li>
 *   <li>{@code AuditRepository} 的 append-only 是"结构上没有 UPDATE/DELETE"，
 *       这正是可以用反射断言的东西（见最后一个用例）。</li>
 * </ul>
 *
 * <p>用独立的 SQLite 文件（{@code java-service/target/test-flow-repo.db}），
 * 不碰开发库 {@code data/mock-oa.db}。
 */
@SpringBootTest(properties = {
        "spring.datasource.url=jdbc:sqlite:target/test-flow-repo.db?busy_timeout=5000"
})
class FlowRepositoryTest {

    @Autowired
    private FlowRepository flowRepository;

    @Autowired
    private AuditRepository auditRepository;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void cleanTables() {
        // 注意：这里用的是裸 JdbcTemplate 而不是 AuditRepository —— 后者根本没有 delete 方法（正是被测的性质）
        jdbcTemplate.update("DELETE FROM audit_log");
        jdbcTemplate.update("DELETE FROM flow");
    }

    private static FlowView flow(String id, String requestId, String state) {
        return new FlowView(id, requestId, state, "MacBook Pro", 1,
                "for new hire", "DEV-01", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
    }

    @Test
    @DisplayName("插入后能按 id 与 request_id 各查到同一张单（幂等查单靠的就是后者）")
    void insertThenFindByIdAndByRequestId() {
        flowRepository.insertFlow(flow("flow-1", "req-1", "PENDING_APPROVAL"));

        Optional<FlowView> byId = flowRepository.findById("flow-1");
        Optional<FlowView> byRequestId = flowRepository.findByRequestId("req-1");

        assertTrue(byId.isPresent());
        assertTrue(byRequestId.isPresent());
        assertEquals("PENDING_APPROVAL", byId.get().state());
        assertEquals(byId.get(), byRequestId.get());
        assertEquals("DEV-01", byId.get().costCenter());
        assertTrue(flowRepository.findById("nope").isEmpty());
    }

    @Test
    @DisplayName("条件更新：预期状态对得上 → 影响 1 行（我抢到了）")
    void updateStateIfCurrentWinsWhenExpectedMatches() {
        flowRepository.insertFlow(flow("flow-2", "req-2", "PENDING_APPROVAL"));

        int updated = flowRepository.updateStateIfCurrent(
                "flow-2", "PENDING_APPROVAL", "APPROVED", "2026-01-01T00:00:01Z");

        assertEquals(1, updated);
        assertEquals("APPROVED", flowRepository.findById("flow-2").orElseThrow().state());
        assertEquals("2026-01-01T00:00:01Z", flowRepository.findById("flow-2").orElseThrow().updatedAt());
    }

    @Test
    @DisplayName("条件更新：预期状态过时 → 影响 0 行（第二个审批人拿不到，FlowService 据此转 409）")
    void updateStateIfCurrentLosesWhenExpectedIsStale() {
        flowRepository.insertFlow(flow("flow-3", "req-3", "PENDING_APPROVAL"));

        // 审批人 A 先赢
        assertEquals(1, flowRepository.updateStateIfCurrent(
                "flow-3", "PENDING_APPROVAL", "APPROVED", "2026-01-01T00:00:01Z"));

        // 审批人 B 手里还是旧的 PENDING_APPROVAL
        int secondCaller = flowRepository.updateStateIfCurrent(
                "flow-3", "PENDING_APPROVAL", "REJECTED", "2026-01-01T00:00:02Z");

        assertEquals(0, secondCaller);
        assertEquals("APPROVED", flowRepository.findById("flow-3").orElseThrow().state());
    }

    @Test
    @DisplayName("条件更新：同一起点只能赢一次（连续两次同样参数，第二次必为 0）")
    void updateStateIfCurrentIsIdempotentInTheWorstWay() {
        flowRepository.insertFlow(flow("flow-4", "req-4", "SUBMITTED"));

        assertEquals(1, flowRepository.updateStateIfCurrent(
                "flow-4", "SUBMITTED", "PENDING_APPROVAL", "2026-01-01T00:00:01Z"));
        assertEquals(0, flowRepository.updateStateIfCurrent(
                "flow-4", "SUBMITTED", "PENDING_APPROVAL", "2026-01-01T00:00:02Z"));
    }

    @Test
    @DisplayName("审计：只追加、按时间升序读回，且 trace_id 落库")
    void auditAppendsAndListsInOrder() {
        auditRepository.insert(new AuditView("audit-1", "flow-5", "tester", "FLOW_CREATED", null, "trace-a", "2026-01-01T00:00:00Z"));
        auditRepository.insert(new AuditView("audit-2", "flow-5", "tester", "SUBMITTED", "{\"k\":1}", "trace-a", "2026-01-01T00:00:01Z"));

        List<AuditView> rows = auditRepository.listByFlow("flow-5");

        assertEquals(2, rows.size());
        assertEquals("FLOW_CREATED", rows.get(0).action());
        assertEquals("SUBMITTED", rows.get(1).action());
        assertEquals("trace-a", rows.get(1).traceId());
        assertEquals(0, auditRepository.listByFlow("other-flow").size());
    }

    @Test
    @DisplayName("审计 append-only 的结构证据：AuditRepository 上不存在任何 update/delete 方法")
    void auditRepositoryHasNoUpdateOrDeleteApi() {
        for (Method method : AuditRepository.class.getDeclaredMethods()) {
            String name = method.getName().toLowerCase();
            assertFalse(name.startsWith("update") || name.startsWith("delete") || name.startsWith("remove"),
                    "AuditRepository 不应该有修改/删除入口，但发现了: " + method.getName());
        }
    }
}
