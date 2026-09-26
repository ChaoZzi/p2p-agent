-- ============================================================================
-- p2p-agent PostgreSQL bootstrap: role + database (P0-02 section 0, run once)
-- p2p-agent 专用 PG 角色与库初始化（P0-02 §0 前置，可重复运行）
-- ----------------------------------------------------------------------------
-- Run it (password prompts are hidden, nothing is written to disk):
--     scripts\pg-init.cmd                     <- recommended (works from any dir)
--   or
--     "D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -f <abs path>
--
-- After it prints "ALL DONE", put the NEW role password into the local,
-- gitignored file:
--     java-service\src\main\resources\application-postgres-local.properties
--         spring.datasource.password=***
-- ============================================================================

\encoding UTF8
\set ON_ERROR_STOP on

\echo '=== 1/4 create role p2p_agent (minimal privilege) ==='
\echo '=== 1/4 建立角色 p2p_agent（最小权限：非超级用户、不能建库/建角色） ==='

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'p2p_agent') THEN
        CREATE ROLE p2p_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

\echo ''
\echo '=== 2/4 set the password for p2p_agent ==='
\echo '=== 2/4 设定 p2p_agent 的密码：请输入两次（不回显，必须真的输入，不要直接回车） ==='
\password p2p_agent

\echo ''
\echo '=== 3/4 verify the role really has a password (has_password must be t) ==='
\echo '=== 3/4 校验角色确实有密码（has_password 必须是 t，否则登录会失败） ==='
SELECT rolname,
       (rolpassword IS NOT NULL AND rolpassword <> '') AS has_password
  FROM pg_authid
 WHERE rolname = 'p2p_agent';

\echo ''
\echo '=== 4/4 create database p2p_agent if missing ==='
\echo '=== 4/4 建立库 p2p_agent（若不存在） ==='
SELECT 'CREATE DATABASE p2p_agent OWNER p2p_agent ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'p2p_agent')
\gexec

\echo ''
\echo '=== result / 结果核对（期望：rolcanlogin=t, rolsuper=f, rolsuper=f；库 owner=p2p_agent, enc=UTF8） ==='
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole
  FROM pg_roles WHERE rolname = 'p2p_agent';
SELECT datname, pg_get_userbyid(datdba) AS owner, pg_encoding_to_char(encoding) AS enc
  FROM pg_database WHERE datname = 'p2p_agent';

\echo ''
\echo '=== ALL DONE / 完成 ==='
\echo 'Next: self-test as the new role (it will ask for the NEW password):'
\echo '下一步：用新角色自测一次（会提示输入你刚设的新密码）'
\echo '    "D:\software\postgres\bin\psql.exe" -U p2p_agent -h 127.0.0.1 -d p2p_agent -c "select current_user, current_database();"'
\echo ''
\echo 'Note: audit_log append-only (REVOKE UPDATE, DELETE) is handled in P0-02 main'
\echo '      task when the table is created by schema-postgres.sql.'
