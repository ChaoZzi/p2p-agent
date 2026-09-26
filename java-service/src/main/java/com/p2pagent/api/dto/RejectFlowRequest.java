package com.p2pagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/** 驳回请求体：{@code {"reason": "..."}}。驳回必须带理由（写进审计的 detail）。 */
public record RejectFlowRequest(@JsonProperty("reason") String reason) {
}
