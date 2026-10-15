package com.p2pagent.db;

import com.p2pagent.api.dto.FlowView;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * {@code flow} 表的唯一出入口（JdbcTemplate）。
 *
 * <p>两条 SQL 都只用两库共通子集（标准 INSERT / SELECT / UPDATE + 参数占位符 {@code ?}），
 * 所以 sqlite 与 postgres 两个 profile 跑的是<b>同一份代码、同一份 SQL</b>。
 *
 * <p><b>核心方法是 {@link #updateStateIfCurrent}</b>：把"我以为的旧状态"写进 WHERE，
 * 用<b>影响行数</b>判断自己是不是第一个改的人。这是 409 FLOW_STATE_CONFLICT 的实现基础 ——
 * 两个审批人同时点"同意"时，只有一个能拿到 1，另一个拿到 0。
 * 它和 P0-01 的幂等（{@code ON CONFLICT DO NOTHING} 看行数）是同一个思想：
 * <b>让数据库来判断"我是不是第一个"，而不是靠应用层的时间差。</b>
 */
@Repository
public class FlowRepository {

    private static final String COLUMNS =
            "id, request_id, state, item, qty, reason, cost_center, created_at, updated_at";

    private static final String INSERT_SQL =
            "INSERT INTO flow(id, request_id, state, item, qty, reason, cost_center, created_at, updated_at) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)";

    /**
     * 幂等插入用的 SQL：与 {@link #INSERT_SQL} 只差结尾的 {@code ON CONFLICT(request_id) DO NOTHING}
     * （用途与原因见 {@link #insertFlowIfAbsent}）。
     */
    private static final String INSERT_IF_ABSENT_SQL =
            "INSERT INTO flow(id, request_id, state, item, qty, reason, cost_center, created_at, updated_at) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(request_id) DO NOTHING";

    private static final String SELECT_BY_ID_SQL = "SELECT " + COLUMNS + " FROM flow WHERE id = ?";

    private static final String SELECT_BY_REQUEST_ID_SQL =
            "SELECT " + COLUMNS + " FROM flow WHERE request_id = ?";

    /** 条件更新（乐观锁）：expected 写进 WHERE，返回影响行数（1 = 我赢了，0 = 别人先改了）。 */
    private static final String UPDATE_STATE_SQL =
            "UPDATE flow SET state = ?, updated_at = ? WHERE id = ? AND state = ?";

    private final JdbcTemplate jdbcTemplate;

    public FlowRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public void insertFlow(FlowView flow) {
        jdbcTemplate.update(INSERT_SQL,
                flow.id(), flow.requestId(), flow.state(), flow.item(), flow.qty(),
                flow.reason(), flow.costCenter(), flow.createdAt(), flow.updatedAt());
    }

    /**
     * 幂等插入：{@code request_id} 已存在时什么都不做，只返回影响行数。
     *
     * <p><b>为什么需要它（而不是"先查再插 + catch 唯一键冲突"）</b>：
     * PostgreSQL 里唯一键冲突会让<b>整个事务进入 aborted 状态</b> —— catch 之后再查库会报
     * {@code current transaction is aborted, commands ignored until end of transaction block}，
     * 请求变成 500。SQLite 没这个问题（约束冲突只影响当条语句），所以<b>只测 SQLite 会漏掉这个坑</b>。
     * 走 {@code ON CONFLICT ... DO NOTHING} 这条"不抛异常"的路，两个库行为一致、事务也不被污染。
     *
     * @return 1 = 是我插入的（调用方应继续推进状态）；0 = 已存在（并发重复请求，调用方应改为返回原单）
     */
    public int insertFlowIfAbsent(FlowView flow) {
        return jdbcTemplate.update(INSERT_IF_ABSENT_SQL,
                flow.id(), flow.requestId(), flow.state(), flow.item(), flow.qty(),
                flow.reason(), flow.costCenter(), flow.createdAt(), flow.updatedAt());
    }

    public Optional<FlowView> findById(String id) {
        return queryOne(SELECT_BY_ID_SQL, id);
    }

    /** 幂等查单：同 request_id 的单据已经存在时，直接把它读回来返回给调用方。 */
    public Optional<FlowView> findByRequestId(String requestId) {
        return queryOne(SELECT_BY_REQUEST_ID_SQL, requestId);
    }

    /**
     * 状态条件更新。
     *
     * @param expectedState 调用方"以为"的当前状态（写进 WHERE 的那个）
     * @param nextState     要迁到的状态
     * @return 影响行数：1 = 更新成功（我抢到了）；0 = 状态已被别人改过，调用方应转 409
     */
    public int updateStateIfCurrent(String id, String expectedState, String nextState, String updatedAt) {
        return jdbcTemplate.update(UPDATE_STATE_SQL, nextState, updatedAt, id, expectedState);
    }

    /**
     * 便于 FlowService 直接传 {@code FlowState} 的便捷重载。
     *
     * <p>参数类型写成 {@code Enum<?>} 而不是 {@code FlowState} 是刻意的：db 层不应该依赖
     * engine 层的枚举（依赖方向：engine → db，不能反过来），同时又不逼调用方自己写
     * {@code .name()}（少一处能写错的地方）。
     */
    public int updateStateIfCurrent(String id, Enum<?> expected, Enum<?> next, String updatedAt) {
        return updateStateIfCurrent(id, expected.name(), next.name(), updatedAt);
    }

    private Optional<FlowView> queryOne(String sql, String argument) {
        List<FlowView> rows = jdbcTemplate.query(sql, FlowRepository::mapRow, argument);
        return rows.isEmpty() ? Optional.empty() : Optional.of(rows.get(0));
    }

    private static FlowView mapRow(ResultSet rs, int rowNum) throws SQLException {
        return new FlowView(
                rs.getString("id"),
                rs.getString("request_id"),
                rs.getString("state"),
                rs.getString("item"),
                rs.getInt("qty"),
                rs.getString("reason"),
                rs.getString("cost_center"),
                rs.getString("created_at"),
                rs.getString("updated_at"));
    }
}
