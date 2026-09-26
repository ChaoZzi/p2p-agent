-- ============================================================
-- P0-02 建表 —— PostgreSQL 16 版（由 spring.sql.init.platform=postgres 选中）
-- 与 schema-sqlite.sql 保持同一份 DDL（共通子集），差异只有两处：
--   ① 文件末尾的 REVOKE：SQLite 没有权限模型，PG 有；
--   ② 将来 PG 专属的能力（JSONB、timestamptz 索引列等）也只能加在这里。
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
    id           TEXT PRIMARY KEY,          -- 业务 id（UUID 字符串），不用 serial/identity
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
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id  TEXT PRIMARY KEY,             -- UUID，不用自增
    flow_id   TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,                -- 见 AuditAction（engine 侧定义）
    detail    TEXT,
    trace_id  TEXT,                         -- 与 TraceFilter 的 [trace=...] 同源，可与 Python 侧对账
    at        TEXT NOT NULL                 -- ISO-8601 字符串
);

-- ============================================================
-- append-only 的数据库层保障（PG 独有，SQLite 做不到）：
-- 把 UPDATE / DELETE 从应用角色身上收掉 —— 越权改审计会被数据库直接拒绝，
-- 而不是"代码里恰好没写"。每次启动重跑一次，保证权限不会被手工改回来。
--
-- 用 CURRENT_USER 而不是写死角色名：本项目的应用角色来自配置占位符
-- （PG_USER，默认 p2p_agent），写死会在换角色时静默失效
-- （REVOKE 一个不存在的角色 → 报错或什么也没做）。
--
-- TRUNCATE 也一起收掉：它在 PG 里是<b>独立权限</b>，一篇 TRUNCATE 就能清空全部审计 ——
-- 和 DELETE 同属"破坏审计完整性"的一类动作，只堵 DELETE 会留下一个显眼的后门。
--
-- 注意（面试可讲的边界）：应用角色同时是表的 owner，owner 理论上能把自己再 GRANT 回来，
-- 所以这是"机制"而不是"绝对不可越权"；真正的强隔离是双角色（migration 角色建表、
-- app 角色只有 INSERT/SELECT）。这一层在 P1 做。
-- ============================================================
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM CURRENT_USER;
