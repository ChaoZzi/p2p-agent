package com.p2pagent.mockoa.api;

import io.swagger.v3.oas.annotations.Operation;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 健康检查：验收第 1 条的靶子。
 *
 * <p>只回 {"status":"ok"}，不带时间戳/版本号——P0-01 只验证"服务在 :8000 活着"，
 * 多余字段会让契约文件越来越像状态面板（那是 P0-02 面板的事）。
 * 响应头 X-Trace-Id 由 TraceFilter 统一回显。
 */
@RestController
public class HealthController {

    @Operation(summary = "健康检查", description = "返回 {\"status\":\"ok\"}；响应头回显 X-Trace-Id")
    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Map.of("status", "ok");
    }
}
