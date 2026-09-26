package com.p2pagent.engine;

import com.p2pagent.api.dto.CreateFlowCommand;
import com.p2pagent.api.dto.FlowView;

/**
 * 流程编排端口（web 层依赖的就是这四个方法）。
 *
 * <p><b>⚠️ 本文件是 P0-02 阶段 A 的占位接口，不是最终形态。</b>
 * 按 P0-02 主体简报 §5，{@code engine/FlowService.java} 属于 [你敲] 文件 ——
 * 阶段 B 由你亲手实现（编排层：校验 → 读状态 → 状态机判定 → 条件更新 → 写审计 → 返回视图）。
 *
 * <p><b>阶段 B 的落地方式（二选一，别混）</b>：
 * <ol>
 *   <li>把本文件直接改成 {@code @Service public class FlowService {...}}（推荐：与规格文档的
 *       文件布局一致，改完记得删除 {@link FlowServiceStub}）；</li>
 *   <li>保留本接口，另写 {@code FlowServiceImpl implements FlowService}（同样要删掉那个桩）。</li>
 * </ol>
 * 只要桩还在、你又新增了一个实现，启动时会报 "expected single matching bean but found 2" ——
 * 这条报错就是提示你回来删它的。
 *
 * <p><b>为什么阶段 A 先钉这个方法签名</b>：web 层（Controller）、Python 侧的 {@code flow_client}
 * 都按它写；签名先定，你的实现换进来时接口两边都不用动。
 *
 * <p>实现要点（详见 docs/task-cards/P0-02-你敲规格-Java.md §3）：每个公开方法 {@code @Transactional}；
 * operator 由 Controller 从 {@code X-Operator} 取出后传参，service 不碰 HttpServletRequest。
 */
public interface FlowService {

    /** 建单（幂等：同 requestId 返回原单）。成功后状态应为 PENDING_APPROVAL，并留下 3 条审计。 */
    FlowView createFlow(String requestId, CreateFlowCommand cmd);

    /** 查单；不存在 → 404 {@code FLOW_NOT_FOUND}。 */
    FlowView getFlow(String flowId);

    /** 审批通过：PENDING_APPROVAL→APPROVED（审计 APPROVED）→ APPROVED→COMPLETED（审计 COMPLETED）。 */
    FlowView approve(String flowId, String operator);

    /** 驳回：PENDING_APPROVAL→REJECTED，审计 detail 里带 reason。 */
    FlowView reject(String flowId, String operator, String reason);
}
