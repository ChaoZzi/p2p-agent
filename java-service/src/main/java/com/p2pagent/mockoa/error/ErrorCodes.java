package com.p2pagent.mockoa.error;

/** 错误码常量：跨语言契约的一部分，Python 侧按 code 分支，不要随手改字符串。 */
public final class ErrorCodes {

    /** 参数缺失 / 形状不对 / 跨字段不一致。 */
    public static final String VALIDATION = "VALIDATION";

    /**
     * 流程状态非法流转（409）。
     *
     * <p>两种触发方式都要映射到它：① 状态机判定 A→B 不合法；② 条件更新影响行数为 0
     * （说明在"读状态"和"改状态"之间有人抢先改了 —— 这就是乐观锁失效的样子）。
     * P0-02 主体起真正生效。
     */
    public static final String FLOW_STATE_CONFLICT = "FLOW_STATE_CONFLICT";

    /** 单据不存在（404）。P0-02 主体新增。 */
    public static final String FLOW_NOT_FOUND = "FLOW_NOT_FOUND";

    /** 能力尚未实现（501）：阶段 A 的桩用它回答，避免"假装成功"。 */
    public static final String NOT_IMPLEMENTED = "NOT_IMPLEMENTED";

    /** 未预期异常。 */
    public static final String INTERNAL = "INTERNAL";

    private ErrorCodes() {
    }
}
