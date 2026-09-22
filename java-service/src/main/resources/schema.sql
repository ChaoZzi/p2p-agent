-- P0-01 幂等表：request_id 为主键，重复请求不新建记录
CREATE TABLE IF NOT EXISTS idempotency (
    request_id    TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    trace_id      TEXT,
    created_at    TEXT NOT NULL
);
