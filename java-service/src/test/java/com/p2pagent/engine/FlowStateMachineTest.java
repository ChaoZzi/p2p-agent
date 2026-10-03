package com.p2pagent.engine;

import com.p2pagent.mockoa.error.ApiException;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 规格才是最终标准。测试和实现都应该对齐规格。
 */
public class FlowStateMachineTest {
    @Test
    void TestTransit(){
        FlowState state = FlowStateMachine.transit(FlowState.DRAFT,FlowState.SUBMITTED);
        assertEquals(FlowState.SUBMITTED,state);
        FlowState state1 = FlowStateMachine.transit(FlowState.SUBMITTED,FlowState.PENDING_APPROVAL);
        assertEquals(FlowState.PENDING_APPROVAL,state1);
        FlowState state2 = FlowStateMachine.transit(FlowState.PENDING_APPROVAL,FlowState.APPROVED);
        assertEquals(FlowState.APPROVED,state2);
        FlowState state3 = FlowStateMachine.transit(FlowState.PENDING_APPROVAL,FlowState.REJECTED);
        assertEquals(FlowState.REJECTED,state3);
        FlowState state4 = FlowStateMachine.transit(FlowState.APPROVED,FlowState.COMPLETED);
        assertEquals(FlowState.COMPLETED,state4);

        ApiException ex1 = assertThrows(ApiException.class,() -> {
            FlowStateMachine.transit(FlowState.REJECTED,FlowState.COMPLETED);
        });
        assertEquals("FLOW_STATE_CONFLICT",ex1.code());

        ApiException ex2 = assertThrows(ApiException.class,() -> {
            FlowStateMachine.transit(FlowState.COMPLETED,FlowState.APPROVED);
        });
        assertEquals("FLOW_STATE_CONFLICT",ex2.code());

    }

    // 真源：由人（照规格表）手写的合法迁移清单 —— 和被测的 ALLOWED 是两份独立的东西
    private static final Set<String> LEGAL = Set.of(
            "DRAFT->SUBMITTED", "SUBMITTED->PENDING_APPROVAL",
            "PENDING_APPROVAL->APPROVED", "PENDING_APPROVAL->REJECTED", "APPROVED->COMPLETED");

    @Test
    void exhaustive_36_combinations() {
        for (FlowState from : FlowState.values()) {
            for (FlowState to : FlowState.values()) {
                String pair = from + "->" + to;                 // 36 个组合一次跑完
                boolean expected = LEGAL.contains(pair);
                assertEquals(expected, FlowStateMachine.canTransit(from, to), "canTransit " + pair);
                if (expected) {
                    assertEquals(to, FlowStateMachine.transit(from, to), "transit " + pair);
                } else {
                    ApiException ex = assertThrows(ApiException.class, () -> FlowStateMachine.transit(from, to), "transit " + pair);
                    assertEquals("FLOW_STATE_CONFLICT", ex.code(), pair);
                }
            }
        }
    }

    @Test
    void nextStates_is_correct_per_state() {
        assertEquals(Set.of(FlowState.SUBMITTED), FlowStateMachine.nextStates(FlowState.DRAFT));
        assertEquals(Set.of(FlowState.APPROVED, FlowState.REJECTED), FlowStateMachine.nextStates(FlowState.PENDING_APPROVAL));
        assertTrue(FlowStateMachine.nextStates(FlowState.COMPLETED).isEmpty());   // 空集，不是 null
        assertTrue(FlowStateMachine.nextStates(FlowState.REJECTED).isEmpty());
    }

    @Test
    void error_message_carries_from_and_to() {                  // 修完 bug 后加这条
        ApiException ex = assertThrows(ApiException.class, () -> FlowStateMachine.transit(FlowState.SUBMITTED, FlowState.COMPLETED));
        /**
         * assertTrue(
         *     ex.getMessage().contains("SUBMITTED -> COMPLETED"),   // 条件
         *     "实际消息: " + ex.getMessage()                         // 失败时打印的信息
         * );
         */
        assertTrue(ex.getMessage().contains("SUBMITTED -> COMPLETED"), "实际消息: " + ex.getMessage());
    }
}
