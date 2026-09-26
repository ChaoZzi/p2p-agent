-- ============================================================
-- P0-02 §0 幂等表 —— SQLite 版（由 spring.sql.init.platform=sqlite 选中）
-- DDL 与 schema-postgres.sql 保持一致：只使用两库共通的最小子集
-- （TEXT / PRIMARY KEY / NOT NULL，无自增、无方言类型）
-- ============================================================
CREATE TABLE IF NOT EXISTS idempotency (
    request_id    TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    trace_id      TEXT,
    created_at    TEXT NOT NULL
);
