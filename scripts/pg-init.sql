-- ============================================================================
-- p2p-agent 专用 PostgreSQL 角色与库（P0-02 §0 前置，一次性执行，可重复运行）
-- ----------------------------------------------------------------------------
-- 用法（在你自己的终端里跑，密码当场输入，不会写进仓库、不会进 shell 历史）：
--     scripts\pg-init.cmd                      ← 一键（推荐，任意目录可跑）
--  或
--     "D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -f <本文件的绝对路径>
--
-- 之后把【新角色】的密码填进【本地未提交】的配置：
--     java-service\src\main\resources\application-postgres-local.properties
--     内容：spring.datasource.password=<你设的密码>
-- （该文件名已被 .gitignore 覆盖；仓库里只有 ${PG_PASSWORD} 占位）
-- ============================================================================

\encoding UTF8
\set ON_ERROR_STOP on

\echo '=== 1/3 建立角色 p2p_agent（最小权限：非超级用户、不能建库/建角色） ==='
\prompt '请为角色 p2p_agent 设定一个密码: ' p2p_pw

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'p2p_agent') THEN
        CREATE ROLE p2p_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER ROLE p2p_agent PASSWORD :'p2p_pw';

\echo '=== 2/3 建立库 p2p_agent（owner=p2p_agent，UTF8） ==='
SELECT 'CREATE DATABASE p2p_agent OWNER p2p_agent ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'p2p_agent')
\gexec

\echo '=== 3/3 结果核对（期望：rolcanlogin=t, rolsuper=f, rolscreatedb=f；库 owner=p2p_agent） ==='
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb FROM pg_roles WHERE rolname = 'p2p_agent';
SELECT datname, pg_get_userbyid(datdba) AS owner, pg_encoding_to_char(encoding) AS enc
  FROM pg_database WHERE datname = 'p2p_agent';

\echo ''
\echo '=== 完成。下一步：用新角色自测一次（会提示输入你刚设的新密码） ==='
\echo '    "D:\software\postgres\bin\psql.exe" -U p2p_agent -h 127.0.0.1 -d p2p_agent -c "select current_user, current_database();"'
\echo ''
\echo '说明：审计表 audit_log 的 append-only 权限（REVOKE UPDATE, DELETE）在 P0-02'
\echo '      主体建表时由 schema-postgres.sql 一并处理；本脚本只管角色与库。'
