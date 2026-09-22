package com.p2pagent.mockoa.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * trace 透传（P0-01 最核心的验收点，契约见 P0-01-dsh-brief.md §3.1）。
 *
 * <p>三条硬要求：
 * <ol>
 *   <li>响应头回显 {@code X-Trace-Id}；</li>
 *   <li>本次请求的每行日志带 {@code [trace=<uuid>]}（放进 MDC，由 logging.pattern.console 输出）；</li>
 *   <li>trace_id 落进幂等记录（由 ApprovalService 读取 MDC 写入 SQLite）。</li>
 * </ol>
 *
 * <p>没带 {@code X-Trace-Id}（或不是 UUID 形状）时自己生成一个，方便直接 curl 调试；
 * 带则<b>原样透传</b>——不重写、不规范化大小写，否则 Python 侧的等值断言必然失败。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceFilter extends OncePerRequestFilter {

    public static final String TRACE_HEADER = "X-Trace-Id";
    public static final String MDC_KEY = "traceId";

    private static final Pattern UUID_SHAPE = Pattern.compile(
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private static final Logger log = LoggerFactory.getLogger(TraceFilter.class);

    /** 供 controller / 异常处理器取当前 trace_id（异常体里要带它）。 */
    public static String currentTraceId() {
        String fromMdc = MDC.get(MDC_KEY);
        return fromMdc != null ? fromMdc : "unknown";
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String incoming = request.getHeader(TRACE_HEADER);
        boolean generated = incoming == null || incoming.isBlank() || !UUID_SHAPE.matcher(incoming.trim()).matches();
        String traceId = generated ? UUID.randomUUID().toString() : incoming.trim();

        MDC.put(MDC_KEY, traceId);
        response.setHeader(TRACE_HEADER, traceId);
        long startedAt = System.nanoTime();
        try {
            log.info("--> {} {} trace_source={}", request.getMethod(), request.getRequestURI(),
                    generated ? "generated" : "incoming");
            chain.doFilter(request, response);
        } finally {
            long costMs = (System.nanoTime() - startedAt) / 1_000_000L;
            log.info("<-- {} {} status={} cost_ms={}", request.getMethod(), request.getRequestURI(),
                    response.getStatus(), costMs);
            MDC.remove(MDC_KEY);
        }
    }
}
