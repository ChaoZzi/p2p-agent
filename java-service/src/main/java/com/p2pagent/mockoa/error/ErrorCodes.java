package com.p2pagent.mockoa.error;

/** 错误码常量：跨语言契约的一部分，Python 侧按 code 分支，不要随手改字符串。 */
public final class ErrorCodes {

    /** 参数缺失 / 形状不对 / 跨字段不一致。 */
    public static final String VALIDATION = "VALIDATION";

    /** 流程状态非法流转。P0-01 没有状态机，先占位，P0-02 生效。 */
    public static final String FLOW_STATE_CONFLICT = "FLOW_STATE_CONFLICT";

    /** 未预期异常。 */
    public static final String INTERNAL = "INTERNAL";

    private ErrorCodes() {
    }
}
