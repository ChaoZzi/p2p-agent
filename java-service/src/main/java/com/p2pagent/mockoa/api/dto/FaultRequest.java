package com.p2pagent.mockoa.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * PUT /api/mock/faults 请求体。
 *
 * <p>两个字段都是包装类型（可为 null）：PUT 支持局部更新——只传 timeout_ms 时
 * random_delay 保持原值。若用 int/boolean 基本类型，Jackson 会把缺失字段填成 0/false，
 * 等于"没传就悄悄关掉开关"，埋雷。
 */
public record FaultRequest(
        @JsonProperty("timeout_ms") Integer timeoutMs,
        @JsonProperty("random_delay") Boolean randomDelay) {
}
