package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 建单的 HTTP 请求体（线上形状）：{@code {request_id, item, qty, reason, cost_center}}。
 *
 * <p>比 {@link CreateFlowCommand} 多一个 request_id —— 这不是重复，而是两个不同的东西：
 * 这里是"传输形状"（要能校验 X-Request-Id 与 body.request_id 一致），
 * 那边是"业务命令"（服务层只关心建什么单）。
 *
 * <p>qty 用包装类型 Integer：字段缺失时拿到 null 才能给出准确的 422 信息；
 * 用 int 的话 Jackson 会静默填 0，变成"数量为 0 的单据"这种脏数据。
 */
public record CreateFlowRequest(
        @JsonProperty("request_id") String requestId,
        @JsonProperty("item") String item,
        @JsonProperty("qty") Integer qty,
        @JsonProperty("reason") String reason,
        @JsonProperty("cost_center") String costCenter) {
}
