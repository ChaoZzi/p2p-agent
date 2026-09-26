package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 建单命令（服务层入参）。
 *
 * <p>刻意<b>不含 request_id</b>：request_id 是"这次请求的身份"，走参数/请求头，
 * 不属于"要建一张什么单"的业务内容（见 P0-02 主体简报 §A2）。
 * 线上 body 里确实带 request_id，但那是为了校验头与体一致（见 {@link CreateFlowRequest}）。
 */
public record CreateFlowCommand(
        @JsonProperty("item") String item,
        @JsonProperty("qty") int qty,
        @JsonProperty("reason") String reason,
        @JsonProperty("cost_center") String costCenter) {
}
