package com.p2pagent.engine;

import com.p2pagent.api.dto.CreateFlowCommand;
import com.p2pagent.api.dto.FlowView;
import com.p2pagent.mockoa.error.ApiException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 阶段 A 的临时桩：让 web 层可以编译、启动、并把"路由 + 参数校验 + 统一错误体 + trace 透传"
 * 这条链路立刻跑通。<b>它不是业务实现，也不允许被当成业务实现。</b>
 *
 * <p><b>⚠️ 阶段 B 请删除本文件</b>（你实现 FlowService 之后）。
 * 留着它的后果很明确：要么启动报 "found 2 beans"，要么（若你把接口改成类）编译报
 * "FlowService is not an interface" —— 两种报错都在提醒同一件事。
 *
 * <p>它回答 501 NOT_IMPLEMENTED 而不是 200 假数据，是刻意的：
 * <b>没实现就说没实现</b>，比返回一个看起来像成功的空对象安全得多（否则前端会以为下单成功了）。
 */
@Component
public class FlowServiceStub implements FlowService {

    private static final Logger log = LoggerFactory.getLogger(FlowServiceStub.class);

    private static final String MESSAGE =
            "FlowService 尚未实现：P0-02 阶段 B 由 [你敲] 完成（engine/FlowService.java），完成后删除 FlowServiceStub";

    @Override
    public FlowView createFlow(String requestId, CreateFlowCommand cmd) {
        log.warn("stub called: createFlow request_id={} (待实现)", requestId);
        throw ApiException.notImplemented(MESSAGE);
    }

    @Override
    public FlowView getFlow(String flowId) {
        log.warn("stub called: getFlow flow_id={} (待实现)", flowId);
        throw ApiException.notImplemented(MESSAGE);
    }

    @Override
    public FlowView approve(String flowId, String operator) {
        log.warn("stub called: approve flow_id={} operator={} (待实现)", flowId, operator);
        throw ApiException.notImplemented(MESSAGE);
    }

    @Override
    public FlowView reject(String flowId, String operator, String reason) {
        log.warn("stub called: reject flow_id={} operator={} (待实现)", flowId, operator);
        throw ApiException.notImplemented(MESSAGE);
    }
}
