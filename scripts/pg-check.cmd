@echo off
REM ===========================================================================
REM Print a definitive verdict about the p2p-agent PostgreSQL bootstrap:
REM   - role p2p_agent exists?  can it log in?  does it HAVE a password?
REM   - database p2p_agent exists?  who owns it?
REM
REM Works from ANY directory. Prompts for the postgres superuser password.
REM Output is also saved to a log file, and the window stays open when the
REM script was launched by double-clicking (so you never lose the verdict).
REM
REM Expect:  has_password = t   and   rolcanlogin = t
REM If has_password shows f (empty), fix it with:
REM   "D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 -c "\password p2p_agent"
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."
set "LOG=%TEMP%\p2p-pg-check.txt"

for /f "tokens=2 delims=:" %%c in ('chcp') do set "_prevcp=%%c"
chcp 65001 >nul

"D:\software\postgres\bin\psql.exe" -U postgres -h 127.0.0.1 ^
  -c "select rolname, rolcanlogin, rolsuper, rolcreatedb, (rolpassword is not null and rolpassword <> '') as has_password from pg_authid where rolname = 'p2p_agent';" ^
  -c "select datname, pg_get_userbyid(datdba) as owner, pg_encoding_to_char(encoding) as enc from pg_database where datname = 'p2p_agent';" %* > "%LOG%" 2>&1
set "_rc=%ERRORLEVEL%"

type "%LOG%"
echo.
echo [i] saved to: %LOG%
if not "%_rc%"=="0" echo [X] psql exited with code %_rc% - see the messages above.

chcp %_prevcp% >nul

REM If launched by double-click, keep the window open so the verdict is readable.
echo %cmdcmdline% | find /i "%~nx0" >nul && (echo. & echo Press any key to close... & pause >nul)
exit /b %_rc%
