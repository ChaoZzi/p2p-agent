@echo off
REM ===========================================================================
REM One-time bootstrap of the p2p-agent PostgreSQL role and database.
REM Works from ANY directory: the script resolves the project root itself.
REM
REM You will be prompted for TWO passwords:
REM   1) the postgres superuser password
REM   2) a NEW password for role p2p_agent  (remember it!)
REM
REM After it succeeds, put the new password into the LOCAL, gitignored file:
REM   java-service\src\main\resources\application-postgres-local.properties
REM       spring.datasource.password=***
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."
"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -f "%ROOT%\scripts\pg-init.sql" %*
