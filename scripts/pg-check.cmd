@echo off
REM ===========================================================================
REM Print a definitive verdict about the p2p-agent PostgreSQL bootstrap:
REM   - role p2p_agent exists?  can it log in?  does it HAVE a password?
REM   - database p2p_agent exists?  who owns it?
REM Works from ANY directory. Prompts for the postgres superuser password.
REM
REM Expect:  has_password = t   and   rolcanlogin = t
REM If has_password shows f (empty) -> run:
REM   "D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -c "\password p2p_agent"
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."

for /f "tokens=2 delims=:" %%c in ('chcp') do set "_prevcp=%%c"
chcp 65001 >nul

"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 ^
  -c "select rolname, rolcanlogin, rolsuper, rolcreatedb, (rolpassword is not null and rolpassword <> '') as has_password from pg_authid where rolname = 'p2p_agent';" ^
  -c "select datname, pg_get_userbyid(datdba) as owner, pg_encoding_to_char(encoding) as enc from pg_database where datname = 'p2p_agent';" %*

set "_rc=%ERRORLEVEL%"
chcp %_prevcp% >nul
exit /b %_rc%
