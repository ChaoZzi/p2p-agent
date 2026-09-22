package com.p2pagent.mockoa.api;

import com.p2pagent.mockoa.api.dto.ApprovalRequest;
import com.p2pagent.mockoa.api.dto.ApprovalResponse;
import com.p2pagent.mockoa.service.ApprovalResult;
import com.p2pagent.mockoa.service.ApprovalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** mock-oa 审批写端点（P0-01 的幂等 + trace 落库验收点）。 */
@RestController
@RequestMapping("/api/mock/oa")
public class ApprovalController {

    private final ApprovalService approvalService;

    public ApprovalController(ApprovalService approvalService) {
        this.approvalService = approvalService;
    }

    /**
     * required=false 是刻意的：缺失时由服务层抛 422 VALIDATION 并带上统一错误体，
     * 而不是让 Spring 抛 MissingRequestHeaderException 出来一个 400 + 默认错误体
     * ——跨语言契约里错误体的形状也必须统一。
     */
    @Operation(summary = "创建审批单（request_id 幂等）",
            description = "缺 X-Request-Id / 必填字段 → 422 VALIDATION；同 request_id 重复调用 → 返回首次结果 + X-Dedup: true")
    @PostMapping("/approvals")
    public ResponseEntity<ApprovalResponse> createApproval(
            @Parameter(description = "幂等键，必须与 body.request_id 一致；缺失 → 422", required = false)
            @RequestHeader(value = "X-Request-Id", required = false) String requestIdHeader,
            @RequestBody(required = false) ApprovalRequest body) {
        ApprovalResult result = approvalService.createApproval(requestIdHeader, body);
        return ResponseEntity.ok()
                .header("X-Dedup", Boolean.toString(result.dedup()))
                .body(result.response());
    }
}
