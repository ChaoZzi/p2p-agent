package com.p2pagent.engine;

public enum AuditAction {
    FLOW_CREATED, //流程已创建
    SUBMITTED,//已提交
    APPROVAL_REQUESTED,//已请求审批
    APPROVED,	//已通过
    REJECTED,	//已拒绝
    COMPLETED,//已完成
    REVISE_REQUESTED//已要求修改
}