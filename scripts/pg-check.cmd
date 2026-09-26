@echo off
REM ===========================================================================
REM Print a definitive verdict about the p2p-agent PostgreSQL bootstrap:
REM   - role p2p_agent exists?  can it log in?  does it HAVE a password?
REM   - database p2p_agent exists?  who owns it?
REM
REM Works from ANY directory. Prompts for the postgres superuser password.
REM The window stays open when launched by double-clicking, so the verdict
REM is never lost.
REM
REM Expect:  has_password = t   and   rolcanlogin = t
REM If has_password shows f (password empty), fix it with:
REM   "D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -c "\password p2p_agent"
REM ===========================================================================
setlocal

for /f "tokens=2 delims=:" %%c in ('chcp') do set "_prevcp=%%c"
chcp 65001 >nul

"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 ^
  -c "select rolname, rolcanlogin, rolsuper, rolcreatedb, (rolpassword is not null and rolpassword <> '') as has_password from pg_authid where rolname = 'p2p_agent';" ^
  -c "select datname, pg_get_userbyid(datdba) as owner, pg_encoding_to_char(encoding) as enc from pg_database where datname = 'p2p_agent';" %*

set "_rc=%ERRORLEVEL%"
chcp %_prevcp% >nul
echo.
if not "%_rc%"=="0" echo [X] psql exited with code %_rc% - see the messages above.

REM Keep the window open if this script was launched by double-clicking.
echo %cmdcmdline% | "%SystemRoot%\System32\find.exe" /i "%~nx0" >nul && (echo Press any key to close... & pause >nul)
exit /b %_rc%
