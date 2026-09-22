package com.p2pagent.mockoa.error;

import org.springframework.http.HttpStatus;

/**
 * 业务异常：携带 HTTP 状态 + 契约里的 code。
 *
 * <p>构造时抛，{@link GlobalExceptionHandler} 统一转成
 * {"error":{"code":..,"message":..,"trace_id":..}} 的错误体。
 */
public class ApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public ApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public static ApiException validation(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, ErrorCodes.VALIDATION, message);
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
