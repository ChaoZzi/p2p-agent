package com.p2pagent.mockoa.error;

import com.p2pagent.mockoa.api.dto.ErrorResponse;
import com.p2pagent.mockoa.web.TraceFilter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 把异常统一收敛成 {"error":{"code","message","trace_id"}}。
 *
 * <p>为什么错误体里也要带 trace_id：跨服务排障时，调用方（Python）拿到 422/500
 * 时能直接带着这串 uuid 去 Java 日志里 grep，不用靠时间戳猜是哪次请求。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ErrorResponse> handleApiException(ApiException ex) {
        log.warn("api error code={} status={} message={}", ex.code(), ex.status().value(), ex.getMessage());
        return ResponseEntity.status(ex.status())
                .body(ErrorResponse.of(ex.code(), ex.getMessage(), TraceFilter.currentTraceId()));
    }

    /** JSON 本身坏了（不是字段缺失）也算参数问题，走同一个 422 + VALIDATION。 */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorResponse> handleUnreadable(HttpMessageNotReadableException ex) {
        log.warn("malformed json body: {}", ex.getMostSpecificCause().getMessage());
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
                .body(ErrorResponse.of(ErrorCodes.VALIDATION, "malformed JSON body", TraceFilter.currentTraceId()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleUnexpected(Exception ex) {
        log.error("unexpected error", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ErrorResponse.of(ErrorCodes.INTERNAL, ex.getClass().getSimpleName(), TraceFilter.currentTraceId()));
    }
}
