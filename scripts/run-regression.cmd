@echo off
REM ===========================================================================
REM Run the P0-02 regression suite (21 assertions) against a running java-service.
REM
REM Why a .cmd wrapper (not just calling pwsh by hand):
REM   1) the console code page is switched to UTF-8 for the run, so the Chinese
REM      output is readable from cmd AND from git-bash;
REM   2) pwsh is called by ABSOLUTE path - a PATH-resolved "pwsh" may resolve to
REM      Windows PowerShell 5.1, which cannot run this script;
REM   3) works from ANY directory (the wrapper resolves the repo root itself).
REM
REM Usage:
REM   scripts\run-regression.cmd                                    (sqlite, :8000)
REM   scripts\run-regression.cmd -Label postgres                     (pg instance)
REM   scripts\run-regression.cmd -BaseUrl http://127.0.0.1:8099 -Label sqlite
REM
REM Exit code: 0 = all 21 assertions passed.
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."

for /f "tokens=2 delims=:" %%c in ('chcp') do set "_prevcp=%%c"
chcp 65001 >nul

"C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -File "%ROOT%\scripts\p0-02-regression.ps1" %*
set "_rc=%ERRORLEVEL%"

chcp %_prevcp% >nul
exit /b %_rc%
