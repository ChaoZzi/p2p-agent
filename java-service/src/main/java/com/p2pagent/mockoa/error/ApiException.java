package com.p2pagent.mockoa.error;

import org.springframework.http.HttpStatus;

// ── 学习笔记：四个静态工厂的分工（已与代码逐行核对）──────────────
//   参数缺失 / 为空 / 形状不对            → validation(...)     422  VALIDATION
//   状态不允许这个操作 / 乐观锁抢输        → conflict(...)       409  FLOW_STATE_CONFLICT
//   找不到东西                            → notFound(...)       404  FLOW_NOT_FOUND
//   我还没实现                            → notImplemented(...) 501  NOT_IMPLEMENTED
// 归纳：这些都是"**受控的、要让调用方知道**"的失败 —— 所以抛出 ApiException 让
// GlobalExceptionHandler 翻译成统一错误体；而"我们自己写崩了"（NPE 之类）不要包装，
// 让它走 Exception 兜底分支，保留 500 + 堆栈（否则 bug 会被伪装成业务错误）。

/**
 * 业务异常：携带 HTTP 状态 + 契约里的 code。
 *
 * <p>构造时抛，{@link GlobalExceptionHandler} 统一转成
 * {"error":{"code":..,"message":..,"trace_id":..}} 的错误体。
 */
public class ApiException extends RuntimeException {

    private final HttpStatus status;

    // 学习笔记：这里和 record 正好相反 ——
    //   record 是"把所有组件都公开"（自动生成同名访问器，如 auditId()）；
    //   普通 class 是"我自己决定暴露什么"：status / code 都是 private final，
    //   类外读不到，只能通过下面两个同名的 public 方法 status() / code() 读；
    //   而且因为是 final，只能在构造器里赋值一次。这就是封装（信息隐藏）。
    private final String code;

    public ApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    /**
     * 422：调用方参数问题（缺失 / 为空 / 形状不对）。
     *
     * <p>收到的调用方应当<b>改参数</b>，原样重试没有意义（所以不是 5xx）。
     */
    public static ApiException validation(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, ErrorCodes.VALIDATION, message);
    }

    /**
     * 状态冲突（409）。
     *
     * <p>message 请带上"from / to / 当前实际状态"这类排障信息 —— 409 是并发与状态机的
     * 交汇点，一句 "conflict" 会让排障者去猜是哪张单、哪一步。
     */
    public static ApiException conflict(String message) {
        return new ApiException(HttpStatus.CONFLICT, ErrorCodes.FLOW_STATE_CONFLICT, message);
    }

    /** 404：资源不存在（例如按 id 查不到那张单）。 */
    public static ApiException notFound(String message) {
        return new ApiException(HttpStatus.NOT_FOUND, ErrorCodes.FLOW_NOT_FOUND, message);
    }

    /** 501：接口已存在但实现还没写（阶段 A 的桩在用，避免假装成功）。 */
    public static ApiException notImplemented(String message) {
        return new ApiException(HttpStatus.NOT_IMPLEMENTED, ErrorCodes.NOT_IMPLEMENTED, message);
    }

    /** 只读访问器：暴露 HTTP 状态码（类外唯一的读法）。 */
    public HttpStatus status() {
        return status;
    }

    /** 只读访问器：暴露契约里的错误码（类外唯一的读法）。 */
    public String code() {
        return code;
    }
}
