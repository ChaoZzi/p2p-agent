-- ============================================================
-- P0-02 §0 幂等表 —— PostgreSQL 16 版（由 spring.sql.init.platform=postgres 选中）
-- 与 schema-sqlite.sql 保持同一份 DDL：证明"共通子集"是可移植的。
-- 主键是业务 id 字符串（request_id），刻意不用 serial / identity 自增。
-- created_at 用 TEXT 存 ISO-8601 字符串（与 HTTP 契约一致，跨库跨语言零类型映射）。
-- 后续（P0-02 主体）：audit_log 建表时在这里追加
--   REVOKE UPDATE, DELETE ON audit_log FROM p2p_agent;
-- 实现 append-only —— 这正是"将来两份 schema 会分叉"的第一个真实原因。
-- ============================================================
CREATE TABLE IF NOT EXISTS idempotency (
    request_id    TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    trace_id      TEXT,
    created_at    TEXT NOT NULL
);
