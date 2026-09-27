package com.p2pagent;

import com.p2pagent.engine.FlowState;
import com.p2pagent.mockoa.error.ApiException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * @SpringBootTest 是什么、有什么用
 * 一句话：它让测试跑起来时，真的启动一个 Spring 容器（应用上下文），这样你才能用 @Autowired 拿到真实的 Bean 来测。
 */

/**
 * |为什么强调"用命令跑，别只在 IDEA 里点"|
 *
 * 核心原因：IDEA 点击和 mvn test 走的是两套不同的执行路径，IDEA 绿 ≠ 命令行绿。
 * 而真正决定代码能不能过的，是命令行那套。
 * 关键：IDEA 点击绕过了 Maven 的生命周期。 它用 IDE 自己的一套来编译和跑，可能忽略 pom.xml 里配置的插件、资源处理、编码设置等。
 *
 * 更接近"别人/线上"的环境
 * 你 IDEA 里可能装了插件、改了配置、有本地缓存的 jar。
 * mvn test 从 ~/.m2 拉依赖，按 pom.xml 来，任何人跑结果一致。
 *
 * CI（持续集成）、打包、别人拉代码后跑，用的都是 Maven 命令，没人点 IDEA。
 * 你 IDEA 绿了，推上去 CI 挂了 → 说明 IDEA 那套和 Maven 那套不一致。
 * 用 mvn test 跑绿，才等于 CI 会绿。
 * 你要保证的是"命令能过"，不是"我 IDE 里能过"。
 */
public class FlowStateTest {
    // ctrl + D 复制当前行到下一行
    @Test
    public void testIsTerminal(){
        boolean result1 = FlowState.DRAFT.isTerminal();
        assertFalse(result1);
        boolean result2 = FlowState.SUBMITTED.isTerminal();
        assertFalse(result2);
        boolean result3 = FlowState.PENDING_APPROVAL.isTerminal();
        assertFalse(result3);
        boolean result4 = FlowState.APPROVED.isTerminal();
        assertFalse(result4);
        boolean result5 = FlowState.REJECTED.isTerminal();
        assertTrue(result5);
        boolean result6 = FlowState.COMPLETED.isTerminal();
        assertTrue(result6);
    }

    @Test
    public void testOf(){
        /**
         * 断言 = 把"我认为应该成立的事"写成机器能自动判真假的检查，跑完直接告诉你对没对。
         * 它不是"打印出来看看"，而是声明预期 → 自动判定 → 汇总结果。
         */
        /**
         * 为什么要把代码包成 () -> ...
         * () -> FlowState.of("draft") 是一个 lambda（匿名函数），代表"这段代码先别执行，等着"。
         * 为什么要包？因为 assertThrows 需要自己控制什么时候执行它——它要在自己搭好的"捕获异常"的保护壳里执行，才能接住异常。
         * 如果直接写：
         * assertThrows(ApiException.class, FlowState.of("draft"));   // ❌ 错
         * 那 FlowState.of("draft") 会在传给 assertThrows 之前就先执行，
         * 异常在调用 assertThrows 前就抛了，assertThrows 根本没机会接住。所以必须包成 lambda 延迟执行。
         */

        /**
         * lambda 为什么会"延迟执行"
         * 核心一句话：lambda 写出来是一个"对象"，不是立刻运行的代码。它把代码包在里面，只有别人调用它时，里面的代码才跑。
         * () -> FlowState.of("draft")
         * 这不是一条执行语句，而是一个值——一个"对象"，类型是某种函数式接口（比如 Executable）。你可以把它想成一个盒子：
         * 盒子内部：FlowState.of("draft")   ← 代码被装进去了，还没跑
         * 你写下这行，只是造了个盒子，里面代码一行都没执行。要跑，得有人打开盒子调用它。
         */
        FlowState res = FlowState.of("DRAFT");
        assertEquals(FlowState.DRAFT,res);

        /**
         * 为什么还要断言 ex.code()
         * ApiException ex = assertThrows(ApiException.class, () -> FlowState.of("draft"));
         * assertEquals("VALIDATION", ex.code());   // 顺便断言错误码
         * 因为 assertThrows 只验了"抛的是 ApiException 类型"，还不够细。ApiException 可能有很多种（notFound、conflict、validation……），
         * 你真正想要的是"参数非法"这一种。
         * 所以把异常接住后，再断言它的 code() 是不是 VALIDATION：
         * 这样测的不只是"抛了异常"，而是"抛了正确类型的、带正确错误码的异常"。
         * 错误码是对外的契约（前端/调用方依赖它），必须锁住。哪天有人把它从 VALIDATION 改成别的，这条测试会立刻红。
         */
        ApiException ex2 = assertThrows(ApiException.class, () -> FlowState.of("draft"));
        assertEquals("VALIDATION", ex2.code());      // 顺便断言错误码是契约的一部分

        ApiException ex3 = assertThrows(ApiException.class, () -> FlowState.of(null));
        assertEquals("VALIDATION",ex3.code());

        ApiException ex4 = assertThrows(ApiException.class, () -> FlowState.of(""));
        assertEquals("VALIDATION",ex4.code());

        ApiException ex5 = assertThrows(ApiException.class, () -> FlowState.of(" "));
        assertEquals("VALIDATION",ex5.code());
    }

}
