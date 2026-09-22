package com.p2pagent.mockoa.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * POST /api/mock/oa/approvals 响应体。
 *
 * <p>{@code dedup}：首次调用 false；同 request_id 重复调用 true（同时响应头 X-Dedup: true）。
 * 注意这个字段是<b>回给调用方看的</b>，幂等表里存的是首次那份 dedup=false 的 JSON。
 */
public record ApprovalResponse(
        @JsonProperty("approval_id") String approvalId,
        @JsonProperty("status") String status,
        @JsonProperty("created_at") String createdAt,
        @JsonProperty("dedup") boolean dedup) {
}
