package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 流程实例视图 —— 对外（HTTP / Python 侧 / 演示页）的<b>唯一</b>形状。
 *
 * <p><b>state 为什么是 String 而不是 FlowState 枚举</b>：这一层是"接口层"，必须能在
 * {@code engine/FlowState}（P0-02 阶段 B 由 [你敲] 提供）之前就编译通过；
 * 而且枚举一旦出现在 DTO 里，Jackson 的序列化行为（名字 / 序号）就变成了跨语言契约的一部分 ——
 * 我们只承诺"库里存的是枚举名、线上传的是这个字符串"。转换发生在 FlowService 的边界上
 * （{@code FlowState.of(row.state())} / {@code state.name()}）。
 *
 * <p>字段线上名一律 snake_case（{@code @JsonProperty}），与 P0-01 的约定一致。
 */
public record FlowView(
        @JsonProperty("id") String id,
        @JsonProperty("request_id") String requestId,
        @JsonProperty("state") String state,
        @JsonProperty("item") String item,
        @JsonProperty("qty") int qty,
        @JsonProperty("reason") String reason,
        @JsonProperty("cost_center") String costCenter,
        @JsonProperty("created_at") String createdAt,
        @JsonProperty("updated_at") String updatedAt) {
}
