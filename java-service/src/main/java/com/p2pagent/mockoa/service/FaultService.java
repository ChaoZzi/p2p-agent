package com.p2pagent.mockoa.service;

import com.p2pagent.mockoa.api.dto.FaultRequest;
import com.p2pagent.mockoa.api.dto.FaultStatus;
import com.p2pagent.mockoa.error.ApiException;
import java.util.concurrent.ThreadLocalRandom;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 故障注入开关（P0-01 只做 timeout / random_delay 两个旋钮）。
 *
 * <p>存在内存里而不是库里：这是"运行期试验开关"，重启即复位是期望行为
 * （否则上次调试留下的 5 秒延迟会变成下一次演示的幽灵 bug）。
 *
 * <p>用 volatile 而不是 AtomicInteger：两个字段各自独立读写、不需要复合原子性，
 * volatile 足够且更直白（代价是两次读可能看到"半新半旧"的组合，对调试开关无所谓）。
 */
@Service
public class FaultService {

    /** timeout_ms 上限：契约规定（P0-01-dsh-brief.md §3.2）。 */
    public static final int MAX_TIMEOUT_MS = 10_000;

    /** random_delay=true 时叠加的随机抖动上限（简报只给了字段名，范围由本实现定义）。 */
    public static final int RANDOM_DELAY_MAX_MS = 500;

    private static final Logger log = LoggerFactory.getLogger(FaultService.class);

    private volatile int timeoutMs = 0;
    private volatile boolean randomDelay = false;

    public FaultStatus status() {
        return new FaultStatus(timeoutMs, randomDelay);
    }

    /** PUT：字段为 null 表示"保持原值"（局部更新），不是"清零"。 */
    public FaultStatus update(FaultRequest request) {
        if (request == null) {
            return status();
        }
        if (request.timeoutMs() != null) {
            int requested = request.timeoutMs();
            if (requested < 0) {
                throw ApiException.validation("timeout_ms must be >= 0");
            }
            int effective = Math.min(requested, MAX_TIMEOUT_MS);
            if (effective != requested) {
                log.warn("timeout_ms={} exceeds max, clamped to {}", requested, MAX_TIMEOUT_MS);
            }
            this.timeoutMs = effective;
        }
        if (request.randomDelay() != null) {
            this.randomDelay = request.randomDelay();
        }
        log.info("fault switch updated timeout_ms={} random_delay={}", timeoutMs, randomDelay);
        return status();
    }

    /**
     * mock 端点调用前调它制造"慢下游"。
     *
     * <p>注意 sleep 的是<b>请求线程</b>：连接被占着，这才是真实的下游超时场景
     * （P0-02 的 Python 侧重试/退避就是被这里逼出来的）。
     */
    public void applyMockDelay() {
        int base = timeoutMs;

        //if randomDelay = true 开启随机抖动 否则 不开启
        int jitter = randomDelay ? ThreadLocalRandom.current().nextInt(0, RANDOM_DELAY_MAX_MS + 1) : 0;

        // 如果total <= 0 代表不睡眠 直接返回
        int total = base + jitter;
        if (total <= 0) {
            return;
        }
        log.info("fault injection: sleeping {} ms (timeout_ms={} random_extra_ms={})", total, base, jitter);
        try {
            Thread.sleep(total);
        } catch (InterruptedException e) {
            //恢复中断 因为线程睡眠了
            Thread.currentThread().interrupt();
            throw new ApiException(org.springframework.http.HttpStatus.INTERNAL_SERVER_ERROR,
                    com.p2pagent.mockoa.error.ErrorCodes.INTERNAL, "fault sleep interrupted");
        }
    }
}
