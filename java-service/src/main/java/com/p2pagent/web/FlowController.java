package com.p2pagent.web;

import com.p2pagent.api.dto.AuditView;
import com.p2pagent.api.dto.CreateFlowCommand;
import com.p2pagent.api.dto.CreateFlowRequest;
import com.p2pagent.api.dto.FlowView;
import com.p2pagent.api.dto.RejectFlowRequest;
import com.p2pagent.db.AuditRepository;
import com.p2pagent.db.FlowRepository;
import com.p2pagent.engine.FlowService;
import com.p2pagent.mockoa.error.ApiException;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 流程端点（P0-02 主体新增）。
 *
 * <p><b>Controller 只做三件事</b>（简报 §A2 钉死）：
 * <ol>
 *   <li>取头/取路参（{@code X-Request-Id} / {@code X-Operator}）；</li>
 *   <li>做"形状级"校验（缺字段、头体不一致 → 422）；</li>
 *   <li>调 {@link FlowService}，把业务异常交给全局处理器映射成统一错误体。</li>
 * </ol>
 * <b>不做状态判断</b>：能不能从 A 迁到 B 是状态机的事，写在 Controller 里就再也测不穷举了。
 *
 * <p>三个刻意的选择：
 * <ul>
 *   <li><b>创建成功返回 200 而不是 201</b>：这个端点是幂等的 —— 重复请求返回的是"原来那张单"，
 *       此时回 201 Created 是撒谎。统一 200 + 资源体，语义才对得上。</li>
 *   <li><b>不返回 X-Dedup</b>：mock-oa 那边有这个头，因为它的响应体里带 dedup 字段；
 *       流程端点这里"原单返回"的事实已经体现在 id/created_at 里，多余的头会让契约变胖。
 *       （若将来需要，加头是一行的事，但要让 FlowService 能把它带出来。）</li>
 *   <li><b>audit 读路径直接走 {@link AuditRepository}</b>：它没有业务规则（只有"存在性检查 + 按序列出"），
 *       再套一层 service 只会多一个转发方法；而<b>写路径</b>（append）必须经过 AuditService
 *       （[你敲] 文件），保证"唯一写入口"这件事不被绕过。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/flows")
public class FlowController {

    private final FlowService flowService;
    private final FlowRepository flowRepository;
    private final AuditRepository auditRepository;

    public FlowController(FlowService flowService, FlowRepository flowRepository, AuditRepository auditRepository) {
        this.flowService = flowService;
        this.flowRepository = flowRepository;
        this.auditRepository = auditRepository;
    }

    @Operation(summary = "创建流程实例（request_id 幂等）",
            description = "body: {request_id, item, qty, reason, cost_center}；X-Request-Id 必须与 body.request_id 一致；成功状态为 PENDING_APPROVAL")
    @PostMapping
    public FlowView createFlow(
            @Parameter(description = "幂等键，必须与 body.request_id 一致；缺失 → 422", required = false)
            @RequestHeader(value = "X-Request-Id", required = false) String requestIdHeader,
            @RequestBody(required = false) CreateFlowRequest body) {

        if (body == null) {
            throw ApiException.validation("request body is required");
        }
        if (isBlank(body.requestId())) {
            throw ApiException.validation("request_id is required");
        }
        if (isBlank(requestIdHeader)) {
            throw ApiException.validation("X-Request-Id header is required");
        }
        if (!body.requestId().trim().equals(requestIdHeader.trim())) {
            throw ApiException.validation("X-Request-Id header must equal body.request_id");
        }
        if (isBlank(body.item())) {
            throw ApiException.validation("item is required");
        }
        if (body.qty() == null || body.qty() < 1) {
            throw ApiException.validation("qty must be >= 1");
        }
        if (isBlank(body.reason())) {
            throw ApiException.validation("reason is required");
        }
        if (isBlank(body.costCenter())) {
            throw ApiException.validation("cost_center is required");
        }

        CreateFlowCommand command = new CreateFlowCommand(
                body.item().trim(), body.qty(), body.reason().trim(), body.costCenter().trim());
        return flowService.createFlow(body.requestId().trim(), command);
    }

    @Operation(summary = "查单据当前状态", description = "不存在 → 404 FLOW_NOT_FOUND")
    @GetMapping("/{id}")
    public FlowView getFlow(@PathVariable String id) {
        return flowService.getFlow(id);
    }

    @Operation(summary = "审批通过", description = "X-Operator 必填；PENDING_APPROVAL→APPROVED→COMPLETED")
    @PostMapping("/{id}/approve")
    public FlowView approve(
            @PathVariable String id,
            @Parameter(description = "审批人，写入审计的 actor", required = false)
            @RequestHeader(value = "X-Operator", required = false) String operator) {
        if (isBlank(operator)) {
            throw ApiException.validation("X-Operator header is required");
        }
        return flowService.approve(id, operator.trim());
    }

    @Operation(summary = "审批驳回", description = "X-Operator 必填；body {reason} 必填，写入审计 detail")
    @PostMapping("/{id}/reject")
    public FlowView reject(
            @PathVariable String id,
            @RequestHeader(value = "X-Operator", required = false) String operator,
            @RequestBody(required = false) RejectFlowRequest body) {
        if (isBlank(operator)) {
            throw ApiException.validation("X-Operator header is required");
        }
        if (body == null || isBlank(body.reason())) {
            throw ApiException.validation("reason is required");
        }
        return flowService.reject(id, operator.trim(), body.reason().trim());
    }

    @Operation(summary = "审计流水（append-only）", description = "按时间升序；单据不存在 → 404")
    @GetMapping("/{id}/audit")
    public List<AuditView> getAudit(@PathVariable String id) {
        if (flowRepository.findById(id).isEmpty()) {
            throw ApiException.notFound("flow not found: " + id);
        }
        return auditRepository.listByFlow(id);
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
