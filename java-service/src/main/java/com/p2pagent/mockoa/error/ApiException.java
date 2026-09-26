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

    /**
     * 状态冲突（409）。
     *
     * <p>message 请带上"from / to / 当前实际状态"这类排障信息 —— 409 是并发与状态机的
     * 交汇点，一句 "conflict" 会让排障者去猜是哪张单、哪一步。
     */
    public static ApiException conflict(String message) {
        return new ApiException(HttpStatus.CONFLICT, ErrorCodes.FLOW_STATE_CONFLICT, message);
    }

    public static ApiException notFound(String message) {
        return new ApiException(HttpStatus.NOT_FOUND, ErrorCodes.FLOW_NOT_FOUND, message);
    }

    /** 501：接口已存在但实现还没写（阶段 A 的桩在用，避免假装成功）。 */
    public static ApiException notImplemented(String message) {
        return new ApiException(HttpStatus.NOT_IMPLEMENTED, ErrorCodes.NOT_IMPLEMENTED, message);
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
