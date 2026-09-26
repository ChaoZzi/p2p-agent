package com.p2pagent.mockoa.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.p2pagent.mockoa.api.dto.ApprovalRequest;
import com.p2pagent.mockoa.api.dto.ApprovalResponse;
import com.p2pagent.mockoa.error.ApiException;
import com.p2pagent.mockoa.error.ErrorCodes;
import com.p2pagent.mockoa.web.TraceFilter;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * 审批创建 + request_id 幂等（P0-01 唯一一个"写"业务）。
 *
 * <p>幂等落库策略：{@code INSERT ... ON CONFLICT(request_id) DO NOTHING} + {@code SELECT} 回读。
 * 为什么不先 SELECT 再 INSERT：那是 check-then-act，两个并发同 request_id 的请求
 * 可以同时查到"不存在"，然后都去插 —— 靠数据库唯一键兜底才是真幂等。
 * 为什么不用 {@code INSERT ... ON CONFLICT DO UPDATE ... RETURNING}：我们不仅要"唯一"，
 * 还要区分这次到底是首次还是重复（首次 dedup=false、日志也不同），两段式更直白。
 *
 * <p><b>P0-02 §0 改动</b>：原写法 {@code INSERT OR IGNORE} 是 SQLite 专有语法，
 * 换成 SQL 标准里被两库共同实现的 UPSERT 形式 {@code ON CONFLICT(...) DO NOTHING}
 * —— PostgreSQL 9.5+ 与 SQLite 3.24+ 都支持，同一份 SQL 在两个 profile 下行为一致。
 * 这条子集边界（"用标准 UPSERT + 显式冲突目标，不用方言专属的 OR IGNORE/OR REPLACE"）
 * 就是本层刻意控制的 SQL 可移植范围，详见 docs/task-cards/P0-02-讲解.md。
 */
@Service
public class ApprovalService {

    private static final Logger log = LoggerFactory.getLogger(ApprovalService.class);

    /**
     * 冲突时静默忽略：0 行受影响 = 这个 request_id 之前来过。
     *
     * <p>写法刻意保持方言中立：冲突目标写成 {@code request_id} 而不是省略
     * （省略 ON CONFLICT 目标在 PG 上仍合法，但显式写出来才对得上 SQLite 的要求，
     * 也让"唯一键是哪一个"在 SQL 里可读）。
     */
    private static final String INSERT_IF_ABSENT_SQL =
            "INSERT INTO idempotency(request_id, response_json, trace_id, created_at) VALUES (?, ?, ?, ?) "
                    + "ON CONFLICT(request_id) DO NOTHING";

    private static final String SELECT_BY_REQUEST_ID_SQL =
            "SELECT response_json, trace_id FROM idempotency WHERE request_id = ?";

    /** 回读重试：sqlite profile（pool=1，写操作串行）下用不到；
     *  postgres profile（pool=10，真并发）下这是必要的防线，详见 selectStored 的注释。 */
    private static final int SELECT_MAX_ATTEMPTS = 20;
    private static final long SELECT_RETRY_MS = 50L;

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final FaultService faultService;

    public ApprovalService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper, FaultService faultService) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.faultService = faultService;
    }

    public ApprovalResult createApproval(String requestIdHeader, ApprovalRequest body) {
        //检验数据
        validate(requestIdHeader, body);

        // 故障注入放在校验之后：参数错了就该立刻 422，别让调试者白等 5 秒
        //422 -> 请求数据合法  但内容不对
        faultService.applyMockDelay();

        String requestId = body.requestId().trim();
        String traceId = TraceFilter.currentTraceId();

        //创建一个审批 状态为pending 待处理  false代表第一次创建
        ApprovalResponse fresh = new ApprovalResponse(
                "oa-" + UUID.randomUUID().toString().substring(0, 8),
                "PENDING",
                Instant.now().toString(),
                false);

        // 插入这条审批 根据返回值inserted判断 =1 则第一次创建 =0 则重复创建
        // ON CONFLICT(request_id) DO NOTHING 代表：若该 requestId 已存在，不插入、不报错，直接返回 0
        // （原 SQLite 专有的 INSERT OR IGNORE 已换成两库通用的 UPSERT 写法，见类注释）
        int inserted = jdbcTemplate.update(INSERT_IF_ABSENT_SQL, requestId, toJson(fresh), traceId, fresh.createdAt());
        if (inserted == 1) {
            log.info("approval created approval_id={} request_id={} flow_id={} applicant={} amount={}",
                    fresh.approvalId(), requestId, body.flowId(), body.applicant(), body.amount());
            return new ApprovalResult(fresh, false);
        }

        // 根据requestId 查询对应记录
        StoredRow stored = selectStored(requestId);
        ApprovalResponse first = fromJson(stored.responseJson());
        ApprovalResponse deduped = new ApprovalResponse(first.approvalId(), first.status(), first.createdAt(), true);
        log.info("idempotency hit request_id={} approval_id={} first_trace_id={} current_trace_id={}",
                requestId, first.approvalId(), stored.traceId(), traceId);
        return new ApprovalResult(deduped, true);
    }

    /** 校验顺序有意为之：先 request_id（契约里唯一给了示例的那条），再其余必填。 */
    private void validate(String requestIdHeader, ApprovalRequest body) {
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
        if (isBlank(body.flowId())) {
            throw ApiException.validation("flow_id is required");
        }
        if (isBlank(body.title())) {
            throw ApiException.validation("title is required");
        }
        if (body.amount() == null || body.amount().signum() < 0) {
            throw ApiException.validation("amount must be a non-negative number");
        }
        if (isBlank(body.applicant())) {
            throw ApiException.validation("applicant is required");
        }
    }

    /** 这个方法sleepQuietly的意义在哪？ 答案：
     sqlite profile：连接池大小为 1（pool=1），所有操作串行，插入和回读之间不会插进别的事务，所以理论上一次就能读到。

     postgres profile（P0-02 §0 之后是真实存在的一条路径，pool=10）：会出现读写并发：

     线程 A 的事务里 INSERT 了 request_id，但还没提交。

     线程 B 的 INSERT ... ON CONFLICT(request_id) DO NOTHING 因为 A 已占用唯一键而被忽略（返回 0）。

     线程 B 立刻回读，但在“读已提交”隔离级别下，读不到 A 还没提交的那行。

     于是线程 B 查不到 → 需要等 A 提交后再读。

     用“最多 20 次 × 50ms = 1 秒”的重试，给 A 的事务留出提交时间。

     注意：JdbcTemplate 在没有外层事务时每条语句各自自动提交（autocommit），
     所以正常情况下 A 的 INSERT 语句返回时就已经提交，B 的 SELECT 能立刻看到；
     这段重试挡的是“将来把这两步放进同一个 @Transactional 里”之后的快照时序问题。
     *
     */
    private StoredRow selectStored(String requestId) {
        for (int attempt = 0; attempt < SELECT_MAX_ATTEMPTS; attempt++) {
            // query方法的格式 List<T> query(String sql, RowMapper<T> rowMapper, Object... args)
            List<StoredRow> rows = jdbcTemplate.query(SELECT_BY_REQUEST_ID_SQL,
                    /**
                     * 第二个参数  RowMapper<T> rowMapper
                     * lambda语法 (参数列表) -> { 方法体 }
                     * lambda 只能用于“函数式接口”，即“只有一个抽象方法”的接口
                     *
                     * 下面的就是在  创建一个匿名类 这个类实现了 RowMapper<StoredRow>
                     * 然后new出这个匿名类的对象 然后重写所实现的RowMapper<StoredRow>接口的抽象方法
                     *
                     * RowMapper<StoredRow> mapper = new RowMapper<StoredRow>() {
                     *     @Override
                     *     public StoredRow mapRow(ResultSet rs, int rowNum) throws SQLException {
                     *         return new StoredRow(rs.getString("response_json"), rs.getString("trace_id"));
                     *     }
                     *     new 一个抽象不完整的类是禁止的——如果类里还有没实现的抽象方法，它就是抽象类，抽象类不能 new
                     *
                     * };
                     *
                     * RowMapper 是接口名，T 决定结果类型。
                     * 但“实际返回的数据”是 query 返回的 List<T> 里的那些对象（由 mapRow 逐个产出），
                     * 而不是 T 本身——T 只是类型，对象才是数据。
                     */
                    (rs, rowNum) -> new StoredRow(rs.getString("response_json"), rs.getString("trace_id")),
                    requestId);
            if (!rows.isEmpty()) {
                return rows.get(0);
            }
            sleepQuietly();
        }
        throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, ErrorCodes.INTERNAL,
                "idempotency record disappeared for request_id=" + requestId);
    }


    private void sleepQuietly() {
        try {
            Thread.sleep(SELECT_RETRY_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, ErrorCodes.INTERNAL, "interrupted while reading idempotency");
        }
    }

    private String toJson(ApprovalResponse response) {
        try {
            return objectMapper.writeValueAsString(response);
        } catch (JsonProcessingException e) {
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, ErrorCodes.INTERNAL, "cannot serialize approval response");
        }
    }

    private ApprovalResponse fromJson(String json) {
        try {
            return objectMapper.readValue(json, ApprovalResponse.class);
        } catch (JsonProcessingException e) {
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, ErrorCodes.INTERNAL, "cannot deserialize stored approval response");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /** 幂等表里存着的那一行。 */
    private record StoredRow(String responseJson, String traceId) {
    }
}
