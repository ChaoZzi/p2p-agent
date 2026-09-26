package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 审计流水的一行（对外形状）。
 *
 * <p>没有 setter、没有 id 之外的可变字段 —— 一行审计一旦写下就只读。
 * {@code action} 是字符串（AuditAction 枚举由 engine 侧定义，理由同 {@link FlowView} 的 state）。
 * {@code traceId} 与日志里的 {@code [trace=...]}、Python 侧 trace JSONL 同源，用于两端对账。
 */
public record AuditView(
        @JsonProperty("audit_id") String auditId,
        @JsonProperty("flow_id") String flowId,
        @JsonProperty("actor") String actor,
        @JsonProperty("action") String action,
        @JsonProperty("detail") String detail,
        @JsonProperty("trace_id") String traceId,
        @JsonProperty("at") String at) {
}
