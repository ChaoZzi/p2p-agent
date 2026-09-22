package com.p2pagent.mockoa.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/** 统一错误体：{"error":{"code":"VALIDATION","message":"...","trace_id":"<uuid>"}}。 */
public record ErrorResponse(@JsonProperty("error") ErrorBody error) {

    public record ErrorBody(
            @JsonProperty("code") String code,
            @JsonProperty("message") String message,
            @JsonProperty("trace_id") String traceId) {
    }

    public static ErrorResponse of(String code, String message, String traceId) {
        return new ErrorResponse(new ErrorBody(code, message, traceId));
    }
}
