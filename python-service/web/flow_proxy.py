"""L0 旁路：把 Java 的流程端点透传给演示页（同源，免 CORS）。

为什么需要这一层：演示页由 FastAPI 在 :8001 提供，Java 在 :8000 —— 浏览器直接调
Java 会被 CORS 拦住；而架构文档（`docs/architecture.md` §2.2）本来就规定
"Python :8001 也提供 GET /api/flows/{id}（透传）"。所以页面只认 :8001，Java 的地址
只存在于服务端。

三条与 tools/flow_client.py 一致的语义：
  1. 每次代理调用**新生成一个 trace_id**（这是页面自己的读请求，不是 chat 那条链的）；
  2. Java 的错误体（status/code/message/trace_id）**原样透出**，页面能显示真实错误码；
  3. Java 不可达 → 503 `JAVA_UNAVAILABLE`，而不是让页面看到 500 堆栈。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from tools import flow_client
from tools.flow_client import ContractError, TracePropagationError, new_trace_id
from web import chat_api

router = APIRouter(prefix="/api/flows", tags=["flows"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _call(fn, *args, **kwargs):
    """统一错误口径：契约错误透出 Java 的 status/code，链路错误给 502/503。"""
    try:
        return fn(*args, **kwargs)
    except ContractError as exc:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message, "trace_id": exc.trace_id}},
        )
    except TracePropagationError as exc:
        return _error(502, "TRACE_PROPAGATION", str(exc))
    except httpx.HTTPError as exc:  # ConnectError / ReadTimeout / ...
        return _error(503, "JAVA_UNAVAILABLE", f"Java backend unreachable: {type(exc).__name__}")


@router.get("/by-request/{request_id}", summary="按 request_id 查刚建的单（演示页用）")
def by_request(request_id: str):
    """只查本进程内存里 chat 工具写下的索引，不打 Java。

    `request_id` 是幂等键（`req-xxxxxxxx`），`flow.id` 是另一个 UUID；
    模型回复里通常复述的是 request_id，页面靠它反查 flow_id。
    """
    hit = chat_api.FLOW_BY_REQUEST.get(request_id)
    if hit is None:
        return _error(404, "REQUEST_NOT_FOUND", f"no flow created in this process for request_id={request_id}")
    return hit


@router.get("/backend-status", summary="Java 是否可达（页面顶部指示灯）")
def backend_status():
    try:
        with httpx.Client(timeout=1.5) as client:
            response = client.get(
                f"{flow_client.DEFAULT_BASE_URL}/api/flows/flow-seed-0001",
                headers={flow_client.TRACE_HEADER: new_trace_id()},
            )
        # 200（单存在）或 404（单不存在）= Java 活着且在应答
        reachable = response.status_code in (200, 404)
        return {"java": "ok" if reachable else "degraded", "java_status": response.status_code}
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=200,
            content={"java": "down", "reason": type(exc).__name__},
        )


@router.get("/{flow_id}", summary="查单据当前状态（透传 Java）")
def get_flow(flow_id: str):
    return _call(flow_client.get_flow, flow_id, trace_id=new_trace_id())


@router.get("/{flow_id}/audit", summary="审计流水（透传 Java，按时间升序）")
def get_audit(flow_id: str):
    return _call(flow_client.get_audit, flow_id, trace_id=new_trace_id())


@router.post("/{flow_id}/approve", summary="审批通过（透传 Java；PENDING_APPROVAL → APPROVED → COMPLETED）")
def approve(flow_id: str, operator: str = Header(default="demo-approver", alias="X-Operator")):
    return _call(flow_client.approve, flow_id, operator=operator, trace_id=new_trace_id())


@router.post("/{flow_id}/reject", summary="审批驳回（透传 Java）")
def reject(flow_id: str, body: dict, operator: str = Header(default="demo-approver", alias="X-Operator")):
    reason = str((body or {}).get("reason") or "").strip()
    if not reason:
        return _error(400, "INVALID_REQUEST", "reason must not be empty")
    return _call(flow_client.reject, flow_id, operator=operator, reason=reason, trace_id=new_trace_id())
