package com.p2pagent.engine;

import com.p2pagent.mockoa.error.ApiException;

public enum FlowState {
    // 每个枚举值在内存里只有一个实例（单例）。
    DRAFT,
    SUBMITTED,
    PENDING_APPROVAL,
    APPROVED,
    REJECTED,
    COMPLETED;

    // 是否是终点
    public boolean isTerminal(){
        return this == COMPLETED || this == REJECTED;
    }

    public static FlowState of(String raw){
        /**
         * String.isBlank()（Java 11+）判断字符串是否为空白，包括：
         * "".isBlank()        // true  空字符串
         * " ".isBlank()       // true  空格
         * "   ".isBlank()     // true  多个空格
         * "\t\n".isBlank()    // true  制表符、换行
         * "  a  ".isBlank()   // false 有非空白字符
         */
        if (raw == null || raw.isBlank()){
            throw ApiException.validation("flow state is blank");
        }

        for (FlowState s : values()){
            if (s.name().equals(raw)) {
                /**
                 * s : FlowState.DRAFT	枚举实例
                 * s.name()	"DRAFT"	拿它的字符串名
                 */
                return s;
            }
        }
        throw ApiException.validation("unknown flow state: " + raw);
    }

}
