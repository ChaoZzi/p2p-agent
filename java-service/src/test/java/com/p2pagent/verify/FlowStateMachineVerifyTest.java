package com.p2pagent.verify;

import com.p2pagent.engine.FlowState;
import com.p2pagent.engine.FlowStateMachine;
import com.p2pagent.mockoa.error.ApiException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertAll;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 【独立验证】FlowStateMachine —— 由 Hermes 编写，不是实现者自测。
 *
 * <p><b>真源</b>：docs/task-cards/P0-02-你敲规格-Java.md §2 的迁移表（下方 {@link #SPEC} 手抄自该表）。
 * <b>独立性</b>：期望值只来自规格，不从被测实现反推 —— 否则实现错了测试也会跟着错。
 *
 * <p>覆盖：36 个状态组合穷举（canTransit / transit 各一轮）、nextStates 逐状态比对、
 * 终态空集语义、返回集合不可变、链式推进、冲突消息的可排障性（31 个非法组合全覆盖）。
 *
 * <p>约定：断言消息一律英文（诊断信息要能在任何控制台 / 报告里读），注释与
 * {@code @DisplayName} 用中文（人读）。
 */
class FlowStateMachineVerifyTest {

    /** 规格 §2 迁移表（手抄，独立真源）。没有出边的状态用空集表示。 */
    private static final Map<FlowState, Set<FlowState>> SPEC = Map.of(
            FlowState.DRAFT, Set.of(FlowState.SUBMITTED),
            FlowState.SUBMITTED, Set.of(FlowState.PENDING_APPROVAL),
            FlowState.PENDING_APPROVAL, Set.of(FlowState.APPROVED, FlowState.REJECTED),
            FlowState.APPROVED, Set.of(FlowState.COMPLETED),
            FlowState.REJECTED, Set.of(),
            FlowState.COMPLETED, Set.of());

    @Test
    @DisplayName("穷举 36 组合：canTransit 的结果必须与规格迁移表逐条一致")
    void exhaustive_canTransit_matches_spec() {
        List<Executable> checks = new ArrayList<>();
        for (FlowState from : FlowState.values()) {
            for (FlowState to : FlowState.values()) {
                String pair = from + "->" + to;
                boolean expected = SPEC.get(from).contains(to);
                checks.add(() -> assertEquals(expected, FlowStateMachine.canTransit(from, to),
                        "canTransit(" + pair + ") must match the spec transition table"));
            }
        }
        assertEquals(36, checks.size(), "exhaustive check must cover 6x6 = 36 combinations");
        assertAll(checks);
    }

    @Test
    @DisplayName("穷举 36 组合：合法迁移返回目标状态，非法迁移抛 409 FLOW_STATE_CONFLICT")
    void exhaustive_transit_matches_spec() {
        List<Executable> checks = new ArrayList<>();
        for (FlowState from : FlowState.values()) {
            for (FlowState to : FlowState.values()) {
                String pair = from + "->" + to;
                if (SPEC.get(from).contains(to)) {
                    checks.add(() -> assertEquals(to, FlowStateMachine.transit(from, to),
                            "legal transition " + pair + " must return the target state"));
                } else {
                    checks.add(() -> {
                        ApiException ex = assertThrows(ApiException.class,
                                () -> FlowStateMachine.transit(from, to),
                                "illegal transition " + pair + " must throw");
                        assertEquals(409, ex.status().value(), pair + " must answer HTTP 409");
                        assertEquals("FLOW_STATE_CONFLICT", ex.code(), pair + " must carry code FLOW_STATE_CONFLICT");
                    });
                }
            }
        }
        assertEquals(36, checks.size());
        assertAll(checks);
    }

    @Test
    @DisplayName("nextStates 逐状态与规格一致（集合比较，不依赖迭代顺序）")
    void nextStates_matches_spec_per_state() {
        List<Executable> checks = new ArrayList<>();
        for (FlowState from : FlowState.values()) {
            checks.add(() -> assertEquals(SPEC.get(from), FlowStateMachine.nextStates(from),
                    "nextStates(" + from + ") must match the spec transition table"));
        }
        assertAll(checks);
    }

    @Test
    @DisplayName("终态没有出边：返回空集而不是 null，且调用方改不动它")
    void terminal_states_and_immutability() {
        assertNotNull(FlowStateMachine.nextStates(FlowState.COMPLETED), "nextStates(COMPLETED) must not be null");
        assertNotNull(FlowStateMachine.nextStates(FlowState.REJECTED), "nextStates(REJECTED) must not be null");
        assertTrue(FlowStateMachine.nextStates(FlowState.COMPLETED).isEmpty(), "COMPLETED is terminal");
        assertTrue(FlowStateMachine.nextStates(FlowState.REJECTED).isEmpty(), "REJECTED is terminal (revise flow is P0-04)");

        // 规格要求：nextStates 返回只读集合 —— 调用方改它必须失败，而不是悄悄改掉全局表
        assertThrows(UnsupportedOperationException.class,
                () -> FlowStateMachine.nextStates(FlowState.DRAFT).add(FlowState.APPROVED),
                "the set returned by nextStates must be unmodifiable");
    }

    @Test
    @DisplayName("transit 的返回值可链式推进：DRAFT -> SUBMITTED -> PENDING_APPROVAL -> APPROVED -> COMPLETED")
    void transit_can_be_chained() {
        FlowState state = FlowState.DRAFT;
        state = FlowStateMachine.transit(state, FlowState.SUBMITTED);
        assertEquals(FlowState.SUBMITTED, state);
        state = FlowStateMachine.transit(state, FlowState.PENDING_APPROVAL);
        assertEquals(FlowState.PENDING_APPROVAL, state);
        state = FlowStateMachine.transit(state, FlowState.APPROVED);
        assertEquals(FlowState.APPROVED, state);
        state = FlowStateMachine.transit(state, FlowState.COMPLETED);
        assertEquals(FlowState.COMPLETED, state);
    }

    @Test
    @DisplayName("冲突消息必须写清真实的 from -> to，并列出合法目标（排障可用）")
    void conflict_message_reports_actual_from_to_and_allowed_targets() {
        List<Executable> checks = new ArrayList<>();
        for (FlowState from : FlowState.values()) {
            for (FlowState to : FlowState.values()) {
                if (SPEC.get(from).contains(to)) {
                    continue;
                }
                String pair = from + "->" + to;
                checks.add(() -> {
                    ApiException ex = assertThrows(ApiException.class,
                            () -> FlowStateMachine.transit(from, to));
                    String msg = ex.getMessage();
                    assertNotNull(msg, pair + ": conflict message must not be null");
                    assertTrue(msg.contains(from + " -> " + to),
                            pair + ": conflict message must state the real requested pair, got: " + msg);
                    assertTrue(msg.contains("allowed"),
                            pair + ": conflict message must list the allowed targets, got: " + msg);
                    for (FlowState allowed : SPEC.get(from)) {
                        assertTrue(msg.contains(allowed.name()),
                                pair + ": conflict message must list allowed target " + allowed + ", got: " + msg);
                    }
                });
            }
        }
        // 36 - 5 合法 = 31 个非法组合，一次报全
        assertEquals(31, checks.size(), "illegal combinations must be 31");
        assertAll(checks);
    }
}
