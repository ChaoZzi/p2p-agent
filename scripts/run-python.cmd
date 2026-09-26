@echo off
REM ============================================================================
REM Python 侧自测：生成 trace_id → 打 Java mock-oa → 断言回显 + 幂等
REM   跑之前 Java 侧必须已在运行（scripts\run-java.cmd）
REM   换地址：scripts\run-python.cmd --base-url http://127.0.0.1:8099
REM ============================================================================
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%\python-service"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
uv run python -m tools.oa_client %*
