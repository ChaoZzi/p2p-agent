"""L0 入口层：POST /api/chat + GET /api/health（P0-02 主体 · L0）。

一次 HTTP 请求里本模块只被调用一次：发工牌号（trace_id）→ 取档案袋（state）→
交给 harness.agent.run（它内部自己跑多轮 ReAct）→ 把档案袋存回去 → 组响应。

**业务判断一行都不写**：查库存、算金额、判审批人是 Java 的事；这里只做
"装配 + 存会话 + 收发"三件事（任务卡 §0 / §10）。

契约（逐字段）：docs/task-cards/P0-02-dsh-任务卡-chat_api.md §3。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from typing import Any, Literal

import pydantic
from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agents.intent import extract
from harness.agent import run
from harness.tool_registry import Tool, ToolRegistry
from tools.flow_client import create_flow, new_trace_id

log = logging.getLogger("chat_api")

router = APIRouter()

#: 会话上限：演示机内存有限，也是"会话不该无限长"的显式承诺。
#: 超过就按插入顺序淘汰最久未用的那个（不做后台清理线程 —— 定时器是这里最不需要的复杂度）。
MAX_SESSIONS = 1000

_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()


# ===========================================================================
# 请求 / 响应模型（字段白名单就是契约：多一个都不给）
# ===========================================================================
class ChatRequest(pydantic.BaseModel):
    """POST /api/chat 的请求体。

    message 为什么是 str | None 而不是必填 str：
    FastAPI 对"必填字段缺失"会返回 **422 + 它自己的错误体**，而契约要求
    **400 + {"error":{"code":"INVALID_REQUEST"}}**。把字段放宽成可选、自己校验，
    才能保证 400/422 的口径只由我们决定（main.create_app() 里另外接住了
    "body 不是 JSON / 类型不对"的 RequestValidationError）。
    """

    session_id: str | None = pydantic.Field(
        default=None,
        description="会话号；首轮不传，服务端生成并在响应里回显；不存在的 id 当作新会话",
    )
    message: str | None = pydantic.Field(
        default=None,
        description="用户这一轮的原话；空串/全空格 → 400，且不会触发 LLM 调用",
    )


class ChatResponse(pydantic.BaseModel):
    """响应体：恰好 5 个字段。

    status 用 Literal 而不是裸 str：三态是**契约的一部分**（前端据此决定"继续追问"
    还是"转人工"），写成字面量类型后它会出现在 OpenAPI 里，谁要加第四种值都得先改这里。
    """

    reply: str = pydantic.Field(description="给用户看的话（可能是反问），原样透传 AgentResult.reply")
    status: Literal["ok", "need_more_info", "give_up"] = pydantic.Field(
        description="ok / need_more_info（在回问用户）/ give_up（该转人工了）"
    )
    session_id: str = pydantic.Field(description="本次会话号（可能是服务端新生成的）")
    trace_id: str = pydantic.Field(description="本次请求的工牌号（uuid4），用于按请求回放")
    tool_calls: list[dict[str, Any]] = pydantic.Field(
        default_factory=list,
        description="本轮真正调过的工具摘要：tool / args / ok / round",
    )


# ===========================================================================
# 统一错误体
# ===========================================================================
def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """契约里的错误形状：{"error":{"code":...,"message":...}}（不夹带堆栈）。"""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


# ===========================================================================
# 会话存储（内存 dict，故意不复杂）
# ===========================================================================
def _take_state(session_id: str) -> dict[str, Any]:
    """取档案袋的**副本**：不把存储里的对象直接交给 agent（它会往里写 trace_id / intent）。

    浅拷贝够用：agent.run 里是 work["intent"] = ... 这种**重绑定**，不会原地改嵌套 dict。
    哪天 agent 改成原地修改 intent，这里就要换 deepcopy。
    """
    with _sessions_lock:
        return dict(_sessions.get(session_id, {}))


def _put_state(session_id: str, state: dict[str, Any]) -> None:
    """存回档案袋，并在超过上限时淘汰最早的会话。三步（取/改/写回）必须整体加锁。"""
    with _sessions_lock:
        _sessions[session_id] = dict(state)
        while len(_sessions) > MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)), None)


def session_count() -> int:
    with _sessions_lock:
        return len(_sessions)


# ===========================================================================
# 两个工具（"师傅名单"）：schema 既喂给模型、也用于校验 —— 同一份对象，不写副本
# ===========================================================================
def _exec_extract_intent(state: dict[str, Any] | None = None, text: str = "") -> dict[str, Any]:
    """工具①：调 agents/intent.py 抽结构化意图（无副作用）。

    抽失败时**诚实告诉模型**（intent=None + note），不编一个默认意图。
    """
    intent, missing = extract(text, (state or {}).get("trace_id", ""))
    if intent is None:
        return {"intent": None, "missing": [], "note": "意图抽取失败，请让用户重说"}
    return {"intent": intent.model_dump(), "missing": missing}


def _exec_create_purchase_flow(
    state: dict[str, Any] | None = None,
    item: str = "",
    qty: int = 1,
    reason: str = "",
    cost_center: str = "",
) -> dict[str, Any]:
    """工具②：真的建单（有副作用）。

    request_id 由**业务身份**（session + 四个业务字段）算哈希得到，不用随机数：
    同一次输入重试必须命中 Java 的幂等（同 request_id 返回原单）；用随机 id 会把
    "重试"变成"下第二张单"。
    """
    current = state or {}
    trace_id = current.get("trace_id") or ""
    if not trace_id:
        # 只有绕过 agent.run 直接调工具才会到这里；给出明确错误而不是 KeyError
        raise RuntimeError("trace_id missing in state (invoke tools through harness.agent.run)")
    request_id = "req-" + hashlib.sha1(
        f'{current.get("session_id")}|{item}|{qty}|{reason}|{cost_center}'.encode()
    ).hexdigest()[:8]
    return create_flow(
        request_id=request_id,
        item=item,
        qty=qty,
        reason=reason,
        cost_center=cost_center,
        trace_id=trace_id,
    )


REGISTRY = ToolRegistry()  # 模块级装配一次：重复装配会撞 duplicate tool name
REGISTRY.register(Tool(
    name="extract_intent",
    description="把用户一句话抽成结构化采购意图；信息不全时返回缺失字段清单",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1}},
        "required": ["text"],
    },
    executor=_exec_extract_intent,
))
REGISTRY.register(Tool(
    name="create_purchase_flow",
    description="用户信息齐全、要真正创建采购单时用（有副作用）",
    parameters={
        "type": "object",
        "properties": {
            "item": {"type": "string", "minLength": 1},
            "qty": {"type": "integer", "minimum": 1, "maximum": 100},
            "reason": {"type": "string", "minLength": 1},
            "cost_center": {"type": "string", "minLength": 1},
        },
        "required": ["item", "qty", "reason", "cost_center"],
    },
    executor=_exec_create_purchase_flow,
    side_effect=True,
))


# ===========================================================================
# 端点
# ===========================================================================
@router.get("/api/health", summary="自检", description="服务活着没 + 当前内存里的会话数")
def health() -> dict[str, Any]:
    return {"status": "ok", "sessions": session_count()}


@router.post(
    "/api/chat",
    response_model=ChatResponse,
    summary="一轮对话",
    description="装配两个工具交给 agent.run；返回 reply / status / session_id / trace_id / tool_calls",
    responses={
        400: {"description": "message 为空或请求体不是合法 JSON（code=INVALID_REQUEST）"},
        500: {"description": "服务端意外（code=INTERNAL，不含堆栈）"},
    },
)
def chat(req: ChatRequest):
    """编号即任务卡 §4 的七步，顺序不要换。"""
    # ① 发工牌号：每次请求一个新 uuid，绝不复用上一轮（否则 trace 文件会串成一锅粥）
    trace_id = new_trace_id()

    # ② message 校验：放在最前面 —— 参数错了立刻 400，别浪费一次 LLM 调用
    message = (req.message or "").strip()
    if not message:
        log.info("chat rejected: empty message trace_id=%s", trace_id)
        return error_response(400, "INVALID_REQUEST", "message must not be empty")

    # ③ 定会话号：不传就生成并回显；不存在的 id 当作新会话（state={}），不报错
    session_id = (req.session_id or "").strip() or ("s-" + uuid.uuid4().hex[:8])

    # ④ 取档案袋（副本），补上会话号与轮次（agent 只写 trace_id / intent）
    state = _take_state(session_id)
    state["session_id"] = session_id
    state["turn"] = int(state.get("turn") or 0) + 1

    log.info("chat start trace_id=%s session_id=%s turn=%s", trace_id, session_id, state["turn"])
    try:
        # ⑤ 调调度员（run 承诺永不抛；真抛了就是我们的装配/环境问题 → 500）
        result = run(message, state=state, trace_id=trace_id, registry=REGISTRY)
        # ⑥ 组响应（字段白名单；Construction 也放进 try：状态值不合约时同样走 500 形状）
        response = ChatResponse(
            reply=result.reply,
            status=result.status,
            session_id=session_id,
            trace_id=trace_id,
            tool_calls=result.tool_calls,
        )
        # ⑦ 存档案袋（存成功才返回，避免"回了用户但下一轮丢了上下文"）
        _put_state(session_id, result.state)
    except Exception:  # noqa: BLE001 —— 边界层兜底：日志留堆栈，响应只给 code
        log.exception("chat internal error trace_id=%s session_id=%s", trace_id, session_id)
        return error_response(500, "INTERNAL", "internal error")

    log.info(
        "chat done trace_id=%s session_id=%s status=%s tools=%s",
        trace_id, session_id, response.status, [c.get("tool") for c in response.tool_calls],
    )
    return response


def install_validation_handler(app: FastAPI) -> None:
    """把"请求体不是合法 JSON / 字段类型不对"统一成 400 INVALID_REQUEST。

    FastAPI 默认会给 422 + 它自己的错误体；契约要的是 400 + 我们的错误形状。
    放在 main.create_app() 里装：这样任何挂载本 router 的 app 都能得到一致的
    错误口径（如果你自己 FastAPI() 后只 include_router 而不调这个函数，
    非法 JSON 会退回 FastAPI 的 422 —— 测试里请用 main.create_app()）。
    """

    @app.exception_handler(RequestValidationError)
    async def _on_request_validation_error(_request, exc: RequestValidationError):  # noqa: ANN001
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        reason = first.get("msg", "invalid request body")
        log.info("chat rejected: invalid request body at %s: %s", location or "-", reason)
        return error_response(
            400, "INVALID_REQUEST", f"invalid request body: {location or 'body'} {reason}".strip()
        )
