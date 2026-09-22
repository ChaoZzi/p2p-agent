package com.p2pagent.mockoa.api;

import com.p2pagent.mockoa.api.dto.FaultRequest;
import com.p2pagent.mockoa.api.dto.FaultStatus;
import com.p2pagent.mockoa.service.FaultService;
import io.swagger.v3.oas.annotations.Operation;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 故障开关端点：PUT 设置 / GET 查当前值。 */
@RestController
@RequestMapping("/api/mock/faults")
public class FaultController {

    private final FaultService faultService;

    public FaultController(FaultService faultService) {
        this.faultService = faultService;
    }

    @Operation(summary = "查询故障开关当前状态")
    @GetMapping
    public FaultStatus status() {
        return faultService.status();
    }

    @Operation(summary = "设置故障开关", description = "timeout_ms 上限 10000（超出被截断）；字段为 null 表示保持原值")
    @PutMapping
    public FaultStatus update(@RequestBody(required = false) FaultRequest request) {
        return faultService.update(request);
    }
}
