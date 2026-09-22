package com.p2pagent.mockoa.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * POST /api/mock/oa/approvals 请求体（snake_case 契约字段，钉死不许改）。
 *
 * <p>用 record + {@code @JsonProperty}：record 组件名是 camelCase（Java 习惯），
 * 线上字段名强制 snake_case——跨语言契约里字段名就是 API 契约本身，
 * 不能依赖"Jackson 全局配置"这种隐式魔法（改一处配置，Python 就 422）。
 */
public record ApprovalRequest(
        @JsonProperty("request_id") String requestId,
        @JsonProperty("flow_id") String flowId,
        @JsonProperty("title") String title,
        @JsonProperty("amount") java.math.BigDecimal amount,
        @JsonProperty("applicant") String applicant) {
}
