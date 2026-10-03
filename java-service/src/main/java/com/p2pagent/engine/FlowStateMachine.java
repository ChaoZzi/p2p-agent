package com.p2pagent.engine;

import com.p2pagent.mockoa.error.ApiException;
import java.util.Map;
import java.util.Set;

public final class FlowStateMachine {
    /**
     * final 修饰的是“变量”本身：这个变量只能被赋值一次。
     * 至于变量里存的东西能不能改，取决于它存的是值还是引用。
     *
     * 变量里存的东西	final 锁住什么	例子
     * 值（基本类型）	值不能改	int、long、boolean、char、double…
     * 引用（对象/数组）	引用不能改，对象内容可改	Map、List、自定义对象、数组
     */
    // 建立映射关系 代表哪个状态可以到达哪个状态
    private static final Map<FlowState, Set<FlowState>> ALLOWED = Map.of(
            FlowState.DRAFT,Set.of(FlowState.SUBMITTED),
            FlowState.SUBMITTED,Set.of(FlowState.PENDING_APPROVAL),
            FlowState.PENDING_APPROVAL,Set.of(FlowState.APPROVED,FlowState.REJECTED),
            FlowState.APPROVED,Set.of(FlowState.COMPLETED),
            FlowState.REJECTED,Set.of(),
            FlowState.COMPLETED,Set.of()
    );

    // 判断是否可以到达
    public static boolean canTransit(FlowState from, FlowState to){
        return nextStates(from).contains(to);
    }

    // 查询下一个状态是什么
    public static Set<FlowState> nextStates(FlowState from){
        Set<FlowState> nextState = ALLOWED.getOrDefault(from,Set.of());
        return nextState;
    }  // Set.of() 只读

    // 根据canTransit判断的结果去实施
    public static FlowState transit(FlowState from, FlowState to){
        if (!canTransit(from,to)){
            throw ApiException.conflict("cannot transit " + from + " -> " + to + " (allowed: " + nextStates(from) + ")");
        }
        return to;
    } // 非法 → 抛 409 FLOW_STATE_CONFLICT
}