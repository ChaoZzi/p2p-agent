package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

// ── 学习笔记：record 语法（Java 16+）─────────────────────────────
// record = 不可变的数据载体。编译器自动生成：
//   ① 全参构造方法
//   ② 所有组件的访问器（方法名就是字段名，如 auditView.auditId()，不是 getAuditId()）
//   ③ equals() / hashCode() / toString()
// "只读"体现在：组件字段编译后都是 private final（构造完成就不能再赋值）、没有 setter，
// 只能通过构造器一次性传入全部值，之后只能读。
//
// 本 record 就是 audit_log 表的一行（对外形状），七个组件与表列一一对应。

/**
 * 审计流水的一行（对外形状）。
 *
 * <p>没有 setter、没有 id 之外的可变字段 —— 一行审计一旦写下就只读。
 * {@code action} 是字符串（AuditAction 枚举由 engine 侧定义，理由同 {@link FlowView} 的 state）。
 * {@code traceId} 与日志里的 {@code [trace=...]}、Python 侧 trace JSONL 同源，用于两端对账。
 */
public record AuditView(
        @JsonProperty("audit_id") String auditId,   // 这条流水自己的编号（UUID，不用自增）
        @JsonProperty("flow_id") String flowId,     // 属于哪张单（逻辑指向 flow.id）
        @JsonProperty("actor") String actor,        // 谁做的：system / 审批人（X-Operator）
        @JsonProperty("action") String action,      // 做了什么：AuditAction 枚举名
        @JsonProperty("detail") String detail,      // 为什么 / 附注（JSON 字符串，可空）
        @JsonProperty("trace_id") String traceId,   // 这次请求的追踪号（与日志、Python 侧对账）
        @JsonProperty("at") String at) {            // 这条流水发生的时刻（ISO-8601 字符串）
}
