package com.p2pagent.mockoa.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/** 故障开关当前状态（PUT 的响应 = GET 的响应，形状一致）。 */
public record FaultStatus(
        @JsonProperty("timeout_ms") int timeoutMs,
        @JsonProperty("random_delay") boolean randomDelay) {
}
