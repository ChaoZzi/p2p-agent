"""FastAPI 应用入口（P0-02 · L0 入口层）。

启动（二选一，都在 python-service 目录下执行）：

    ./.venv/Scripts/python.exe main.py                  # 监听 127.0.0.1:8001
    ./.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001

前置：Java 侧在 :8000 跑着（python-service/tools/flow_client.py 的默认 base_url），
并且 python-service/.env 里有 DEEPSEEK_API_KEY（gitignored，不入库）。

端口：Java 占 8000，本服务占 8001（docs/architecture.md §2.2）。
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from web import chat_api

HOST = "127.0.0.1"
PORT = 8001


def create_app() -> FastAPI:
    """装配 app。

    做成函数而不仅是模块级 app：测试需要一个干净实例（或要重新装配 router）时，
    不必依赖 import 副作用；生产/调试仍然用下面的模块级 app。
    """
    app = FastAPI(
        title="P2P Agent - L0 chat API",
        version="0.1.0",
        description=(
            "L0 入口层：一轮对话 = 装配工具（registry）→ agent.run → 存会话 state → 返回 "
            "reply/status/session_id/trace_id/tool_calls"
        ),
    )
    app.include_router(chat_api.router)
    # 非法 JSON / 字段类型不对 → 400 INVALID_REQUEST（契约 §3.1），而不是 FastAPI 默认的 422
    chat_api.install_validation_handler(app)
    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    uvicorn.run(app, host=HOST, port=PORT)
