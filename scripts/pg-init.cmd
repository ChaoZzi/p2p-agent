@echo off
REM ===========================================================================
REM One-time bootstrap of the p2p-agent PostgreSQL role and database.
REM Works from ANY directory: the script resolves the project root itself.
REM
REM Prompts (all hidden, nothing is stored on disk):
REM   1) the postgres superuser password
REM   2) a NEW password for role p2p_agent, typed TWICE (do not just press Enter)
REM
REM Console is switched to UTF-8 for the run (so the Chinese hints are readable)
REM and switched back afterwards.
REM
REM After it prints "ALL DONE", put the new password into the LOCAL gitignored
REM file:
REM   java-service\src\main\resources\application-postgres-local.properties
REM       spring.datasource.password=***
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."

for /f "tokens=2 delims=:" %%c in ('chcp') do set "_prevcp=%%c"
chcp 65001 >nul

"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -f "%ROOT%\scripts\pg-init.sql" %*
set "_rc=%ERRORLEVEL%"

chcp %_prevcp% >nul
if not "%_rc%"=="0" (
  echo.
  echo [X] psql exited with code %_rc% - see the messages above.
  exit /b %_rc%
)
