@echo off
REM ===========================================================================
REM Python side self-test: generates a trace_id, calls Java mock-oa, asserts the
REM echoed trace_id and the idempotency semantics (dedup=false then dedup=true).
REM The Java side must already be running (scripts\run-java.cmd).
REM
REM Uses the project venv (python-service\.venv) so that it does NOT depend on
REM uv being on PATH - same interpreter PyCharm uses. Falls back to uv if the
REM venv is missing (then it bootstraps it once).
REM
REM Override the base URL:
REM   scripts\run-python.cmd --base-url http://127.0.0.1:8099
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%\python-service"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" goto :use_venv

echo [!] python-service\.venv not found - trying to bootstrap it with uv...
where uv >nul 2>nul
if errorlevel 1 (
  echo [X] uv is not on PATH either. Install uv, or create the venv manually:
  echo     cd python-service ^&^& uv sync
  exit /b 1
)
uv sync || exit /b 1
uv run python -m tools.oa_client %*
goto :eof

:use_venv
".venv\Scripts\python.exe" -m tools.oa_client %*
