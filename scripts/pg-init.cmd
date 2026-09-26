@echo off
REM ============================================================================
REM 一次性初始化 p2p-agent 的 PostgreSQL 角色与库（任意目录都能跑）
REM   会提示输入两次口令：
REM     1) postgres 超级用户的口令
REM     2) 你给新角色 p2p_agent 设定的新口令（记住它，要写进本地未提交的配置）
REM   跑完把新口令写进：
REM     java-service\src\main\resources\application-postgres-local.properties
REM         spring.datasource.password=***
REM ============================================================================
setlocal
set "ROOT=%~dp0.."
"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -f "%ROOT%\scripts\pg-init.sql" %*
