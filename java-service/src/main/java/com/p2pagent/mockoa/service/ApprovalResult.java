package com.p2pagent.mockoa.service;

import com.p2pagent.mockoa.api.dto.ApprovalResponse;

/**
 * 服务层返回值：业务体 + 是否命中幂等。
 *
 * <p>为什么把 dedup 单独带出来而不只看 body 里的 dedup 字段：controller 要据此设置
 * 响应头 {@code X-Dedup}——契约要求"重复请求 → 响应头 X-Dedup: true"，
 * 响应头是给网关/客户端拦截器读的，body 是给业务代码读的，两者都得有。
 */
public record ApprovalResult(ApprovalResponse response, boolean dedup) {
}
