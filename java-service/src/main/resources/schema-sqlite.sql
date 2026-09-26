-- ============================================================
-- P0-02 建表 —— SQLite 版（由 spring.sql.init.platform=sqlite 选中）
-- 只使用两库共通的最小子集：TEXT / INTEGER / PRIMARY KEY / UNIQUE / NOT NULL，
-- 不用自增（AUTOINCREMENT vs IDENTITY 是方言差异重灾区），主键一律用业务生成的字符串 id。
-- 时间字段一律 TEXT + ISO-8601 字符串（与 HTTP 契约一致，跨库跨语言零类型映射）。
-- ============================================================

-- 幂等表：request_id 是主键。P0-01 建立，P0-02 沿用（语义不变）。
CREATE TABLE IF NOT EXISTS idempotency (
    request_id    TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    trace_id      TEXT,
    created_at    TEXT NOT NULL
);

-- 流程实例表：一张单一行，state 是 FlowState 枚举名（权威定义在 Java 的 engine/FlowState）。
CREATE TABLE IF NOT EXISTS flow (
    id           TEXT PRIMARY KEY,          -- 业务 id（UUID 字符串），不用自增
    request_id   TEXT NOT NULL UNIQUE,      -- 与 idempotency 同源：创建接口用它做幂等
    state        TEXT NOT NULL,             -- DRAFT / SUBMITTED / PENDING_APPROVAL / ...
    item         TEXT NOT NULL,
    qty          INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    cost_center  TEXT NOT NULL,
    created_at   TEXT NOT NULL,             -- ISO-8601 字符串
    updated_at   TEXT NOT NULL
);

-- 审计流水表：append-only。
-- SQLite 没有权限模型（没有 GRANT/REVOKE），所以这一层的 append-only 只能靠：
--   ① 代码层：AuditRepository 只提供 insert / listByFlow，没有任何 UPDATE/DELETE 语句；
--   ② 数据库层的等效手段在 schema-postgres.sql 里（REVOKE UPDATE, DELETE）。
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id  TEXT PRIMARY KEY,             -- UUID，不用自增
    flow_id   TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,                -- 见 AuditAction（engine 侧定义）
    detail    TEXT,
    trace_id  TEXT,                         -- 与 TraceFilter 的 [trace=...] 同源，可与 Python 侧对账
    at        TEXT NOT NULL                 -- ISO-8601 字符串
);
