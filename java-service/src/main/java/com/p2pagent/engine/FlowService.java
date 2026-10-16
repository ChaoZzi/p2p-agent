package com.p2pagent.engine;


import com.p2pagent.api.dto.CreateFlowCommand;
import com.p2pagent.api.dto.FlowView;
import com.p2pagent.db.FlowRepository;
import com.p2pagent.mockoa.error.ApiException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;


@Service
public class FlowService {
    private final FlowRepository flowRepository;

    private final AuditService auditService;


    public FlowService(FlowRepository flowRepository,AuditService auditService){
        this.flowRepository = flowRepository;
        this.auditService = auditService;

    }

    /** 建单（幂等：同 requestId 返回原单）。成功后状态应为 PENDING_APPROVAL，并留下 3 条审计。 */
    @Transactional
    public FlowView createFlow(String requestId, CreateFlowCommand cmd){
        Optional<FlowView> existed = flowRepository.findByRequestId(requestId);
        // 如果有值
        if (existed.isPresent()){
            return existed.get();
        }

        String id = UUID.randomUUID().toString();
        String time = Instant.now().toString();

        FlowView view = new FlowView(id,requestId,FlowState.DRAFT.name(),
                cmd.item(),cmd.qty(), cmd.reason(),cmd.costCenter(),time,
                time);

        int row = flowRepository.insertFlowIfAbsent(view);
        if (row == 0){
            return flowRepository.findByRequestId(requestId).orElseThrow(() ->
                    ApiException.notFound("flow not found after conflict" + requestId));
        }

        auditService.append(id,"system",AuditAction.FLOW_CREATED,
                "{\"item\":\"" + cmd.item() + "\",\"qty\":" + cmd.qty() + "}");

        FlowStateMachine.transit(FlowState.DRAFT, FlowState.SUBMITTED);
        String submittedTime = Instant.now().toString();
        int affectrow = flowRepository.updateStateIfCurrent(id, FlowState.DRAFT, FlowState.SUBMITTED,submittedTime);
        if (affectrow == 0){
            throw ApiException.conflict("state changed while submitting flow " + view.id());
        }
        auditService.append(id,"system",AuditAction.SUBMITTED,
                "{}");

        FlowStateMachine.transit(FlowState.SUBMITTED, FlowState.PENDING_APPROVAL);
        String pending_approval_time = Instant.now().toString();
        int i = flowRepository.updateStateIfCurrent(id, FlowState.SUBMITTED, FlowState.PENDING_APPROVAL, pending_approval_time);
        if (i == 0) {
            throw ApiException.conflict("state changed from submitted to pending_approval "+ view.id());
        }

        auditService.append(id,"system",AuditAction.APPROVAL_REQUESTED,"{}");
        FlowView flowView = getFlow(id);
        return flowView;

    }

    /** 查单；不存在 → 404 {@code FLOW_NOT_FOUND}。 */
    public FlowView getFlow(String flowId){
        Optional<FlowView> flowView = flowRepository.findById(flowId);
        return flowView.orElseThrow(() -> ApiException.notFound("flow not found: " + flowId));
    }

    /** 审批通过：PENDING_APPROVAL→APPROVED（审计 APPROVED）→ APPROVED→COMPLETED（审计 COMPLETED）。 */
    @Transactional
    public FlowView approve(String flowId, String operator){
        String approval_time = Instant.now().toString();
        FlowView flowView = getFlow(flowId);
        FlowState flowState = FlowState.of(flowView.state());
        FlowStateMachine.transit(flowState, FlowState.APPROVED);
        int i = flowRepository.updateStateIfCurrent(flowId, flowState, FlowState.APPROVED, approval_time);
        if (i == 0){
            throw ApiException.conflict("approve raced: flow " + flowId + " is not " +flowState +" anymore");
        }

        auditService.append(flowId,operator,AuditAction.APPROVED,"{}");
        FlowStateMachine.transit(FlowState.APPROVED,FlowState.COMPLETED);
        String completed_time = Instant.now().toString();
        int affectrow = flowRepository.updateStateIfCurrent(flowId, FlowState.APPROVED, FlowState.COMPLETED, completed_time);
        if (affectrow == 0){
            throw ApiException.conflict("approve raced: flow " + flowId + " is not approved anymore");
        }

        auditService.append(flowId,operator,AuditAction.COMPLETED,"{}");
        return getFlow(flowId);

    }

    /** 驳回：PENDING_APPROVAL→REJECTED，审计 detail 里带 reason。 */
    @Transactional
    public FlowView reject(String flowId, String operator, String reason){
        String reject_time = Instant.now().toString();
        FlowView flowView = getFlow(flowId);
        FlowState flowState = FlowState.of(flowView.state());
        FlowStateMachine.transit(flowState,FlowState.REJECTED);
        int i = flowRepository.updateStateIfCurrent(flowId, flowState,FlowState.REJECTED, reject_time);
        if (i == 0){
            throw ApiException.conflict("flow" + flowId + "is rejected");
        }

        auditService.append(flowId,operator,AuditAction.REJECTED,"{\"reason\":\"" + reason + "\"}");
        return getFlow(flowId);

    }
}
