"""trace 落盘（P0-02 主体 A3）：一个 trace 一个文件，一行一个 JSON。

钉死的接口（[你敲] 的 harness/agent.py、tool_registry.py 只依赖这一个函数）：

    record(trace_id: str, step: str, payload: dict) -> None

为什么是 JSONL 而不是写数据库：
  - 追加写 + 一行一记录 = 崩溃时最多丢最后一行（数据库事务在这个场景是过度设计）；
  - 纯文本，出问题能直接看、能直接 grep，不依赖任何工具；
  - Java 侧审计落在库里（要按单查询、要权限保护），Python 侧 trace 落在文件里（要看细节、要快），
    两边用同一个 trace_id 对账 —— 一条请求跨两个进程、两种存储，靠一个 uuid 串起来。

失败语义（刻意不对称，见文档 §"改了会怎样"）：
  - 目录建不出来 → 直接抛 TracerError（配置/权限坏了，早失败）；
  - 单次追加失败 → 只记日志、不打断业务（可观测性不该成为业务故障的来源）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_lock = threading.Lock()


class TracerError(RuntimeError):
    """trace 目录不可用（配置错误），需要立刻让使用方知道。"""


def trace_dir() -> Path:
    """trace 目录：默认 <repo>/trace，可用 TRACE_DIR 环境变量覆盖（测试用临时目录）。"""
    return Path(os.environ.get("TRACE_DIR") or (REPO_ROOT / "trace"))


def record(trace_id: str, step: str, payload: dict[str, Any]) -> None:
    """追加一条 trace。

    :param trace_id: 贯穿一次请求的 uuid（Python 生成 → HTTP 头 → Java 日志/审计 → 这里）
    :param step: 语义化的步骤名，如 "intent.extract" / "tool.create_purchase_flow"
    :param payload: 任意可 JSON 序列化的细节（原始输入、模型输出、耗时、成败…）
    """
    if not trace_id:
        raise ValueError("trace_id is required")

    directory = trace_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TracerError(f"cannot create trace directory {directory}: {exc}") from exc

    entry = {
        "trace_id": trace_id,
        "step": step,
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    path = directory / f"{trace_id}.jsonl"

    # 加锁：一行一次 write，避免多线程下两行交叠成半行 JSON（不可解析的 trace 等于没记）
    with _lock:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            # 见模块 docstring：可观测性写失败不打断业务，但必须留下痕迹
            print(f"[tracer] WARN cannot append trace {path}: {exc}")


def read_trace(trace_id: str) -> list[dict[str, Any]]:
    """读回一次请求的全部 trace（P0-02 阶段 C 的"回放视图"要用）。"""
    path = trace_dir() / f"{trace_id}.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            entries.append(json.loads(raw))
    return entries
