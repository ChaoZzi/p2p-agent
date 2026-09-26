package com.p2pagent.db;

import com.p2pagent.api.dto.AuditView;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * {@code audit_log} 表的唯一出入口 —— <b>append-only 的第一层保障就写在"这个类没有哪些方法"里</b>。
 *
 * <p>整个类只有两个 SQL：一条 INSERT、一条 SELECT。没有 update、没有 delete、
 * 没有任何"改一行"的入口 —— 这不是靠自觉，而是<b>结构上不存在这个能力</b>：
 * 想改一条审计，必须先在这个类里新加一个方法，那是一次会被 review 看见的改动。
 *
 * <p>另外两层保障（见 docs/task-cards/P0-02-主体-讲解.md）：
 * 数据库层（PG {@code REVOKE UPDATE, DELETE}）、业务层（状态变更与审计写入在同一事务；每次迁移必写一条）。
 */
@Repository
public class AuditRepository {

    private static final String INSERT_SQL =
            "INSERT INTO audit_log(audit_id, flow_id, actor, action, detail, trace_id, at) "
                    + "VALUES (?, ?, ?, ?, ?, ?, ?)";

    /** 按时间升序；同一毫秒内的两条用 audit_id 兜底排序，保证顺序稳定可断言。 */
    private static final String SELECT_BY_FLOW_SQL =
            "SELECT audit_id, flow_id, actor, action, detail, trace_id, at FROM audit_log "
                    + "WHERE flow_id = ? ORDER BY at ASC, audit_id ASC";

    private final JdbcTemplate jdbcTemplate;

    public AuditRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public void insert(AuditView entry) {
        jdbcTemplate.update(INSERT_SQL,
                entry.auditId(), entry.flowId(), entry.actor(), entry.action(),
                entry.detail(), entry.traceId(), entry.at());
    }

    public List<AuditView> listByFlow(String flowId) {
        return jdbcTemplate.query(SELECT_BY_FLOW_SQL, AuditRepository::mapRow, flowId);
    }

    private static AuditView mapRow(ResultSet rs, int rowNum) throws SQLException {
        return new AuditView(
                rs.getString("audit_id"),
                rs.getString("flow_id"),
                rs.getString("actor"),
                rs.getString("action"),
                rs.getString("detail"),
                rs.getString("trace_id"),
                rs.getString("at"));
    }
}
