from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pydantic

from harness.tool_registry import ToolRegistry
from llm.deepseek import complete_json
from trace.tracer import record

STEP = "agent.run"
TOOL_PREFIX = "tool:"
MAX_ROUNDS = 3                      # 护栏 1：轮数上限（防死循环 + 防烧钱）
MAX_CONSECUTIVE_FAILURES = 2        # 护栏 2：同一工具连续失败这么多次就收口
MAX_OBSERVATION_CHARS = 2000        # 护栏 3：观察结果截断（防 token 爆炸）

BASE_SYSTEM = (
    "你是企业采购流程的调度器。每一轮只输出一个 JSON 对象，字段：\n"
    "  thought: 一句话判断\n"
    '  action:  "tool:<工具名>" 调用工具；"final" 结束本轮\n'
    "  args:    调用工具的参数对象（action 为 final 时给 {}）\n"
    "  reply:   action 为 final 时给用户看的话\n"
    "  need_more_info: 布尔；若你是在回问用户、要用户补充信息，填 true\n"
    "规则：\n"
    "1) 只能用工具清单里的工具，禁止编造工具名；\n"
    "2) 缺少必要信息时不要猜，用 final + 提问收尾；\n"
    "3) 工具返回 ok=false 时读懂 error.detail：能补参数就补，不能就提问或放弃；\n"
    "4) 不要用同样的参数重复调用同一个工具。"
)


class Decision(pydantic.BaseModel):
    """模型每一轮必须产出的形状（它的 model_json_schema() 会喂给模型）。"""

    thought: str = pydantic.Field(default="", max_length=500)
    action: str = pydantic.Field(min_length=1)                   # "tool:<name>" 或 "final"
    args: dict[str, Any] = pydantic.Field(default_factory=dict)   # 调工具时的参数
    reply: str | None = None                                      # final 时给用户的话
    need_more_info: bool = False        # final 时：我这是在回问用户（而不是给结论）

@dataclass
class AgentResult:
    reply: str                                   # 给用户看的话（可能是反问）
    state: dict[str, Any]                        # 更新后的会话状态（调用方负责持久化）
    tool_calls: list[dict[str, Any]] = field(default_factory=list)   # 本轮调用记录（页面侧栏/审计）
    status: str = "ok"                           # ok | need_more_info | give_up

#① run() 的四个入参 —— 实际值
#user_text	"我想申请采购 1 台 MacBook Pro，给新来的前端用"	用户原话（chat_api 从 HTTP body 取）
#state	{"session_id": "s-42", "turn": 1}	chat_api 从内存 dict 取 —— 注意：本轮还没有 trace_id
#trace_id	"3f2a6c1e-9b44-4d7a-8c21-acde00000042"	chat_api 用 new_trace_id() 生成（uuid4 字符串）
#registry	两个工具，每个含 name/description/parameters/executor/side_effect	装配者 chat_api.py（dsh）
def run(user_text: str, *, state: dict[str, Any], trace_id: str,
        registry: ToolRegistry, max_rounds: int = MAX_ROUNDS) -> AgentResult:
    """跑一次对话轮次。**永不抛**：任何失败都变成 status 里的结论。"""
    work = dict(state or {}) # 不改调用方传进来的对象
    work["trace_id"] = trace_id # registry.invoke 靠它写 trace
    history : list[str] = []
    tool_calls : list[dict[str,Any]] = []
    last_tool : str | None = None
    failures = 0

    for round_no in range(1,MAX_ROUNDS + 1):
        decision = _decide(user_text, registry, history, trace_id, round_no, work)
        if decision is None:  # 两次都解析不出可用的决策
            return AgentResult(
                reply="抱歉，我没能把模型输出整理成可执行的决策（已重试一次），已转人工。",
                state=work, tool_calls=tool_calls, status="give_up")

        if decision.action == "final":  # 护栏 4：反问也是 final
            reply = (decision.reply or "").strip()
            return AgentResult(reply=reply, state=work, tool_calls=tool_calls,
                               status=_final_status(reply, decision.need_more_info))

        if not decision.action.startswith(TOOL_PREFIX):
            history.append(f"[round {round_no}] 非法 action={decision.action!r}: "
                           f"只能是 {TOOL_PREFIX}<工具名> 或 final")
            continue
        name = decision.action[len(TOOL_PREFIX):].strip()
        args = decision.args or {}
        result = registry.invoke(name, args, work)  # 不抛：registry 保证
        tool_calls.append({"tool": name, "args": args,
                           "ok": bool(result.get("ok")), "round": round_no})
        history.append(f"[round {round_no}] {name}({_compact(args)}) -> {_observation(result)}")

        if result.get("ok"):
            last_tool, failures = name, 0
            data = result.get("data") or {}  # 工具成功的返回（dict）
            if isinstance(data, dict) and data.get("intent"):
                work["intent"] = data["intent"]  # ★ 已确认的意图必须留在 state 里，见下方说明
            continue

        failures = failures + 1 if name == last_tool else 1  # 护栏 2：连续失败计数
        last_tool = name
        if failures >= MAX_CONSECUTIVE_FAILURES:
            detail = (result.get("error") or {}).get("detail", "")
            return AgentResult(
                reply=f"{name} 连续 {failures} 次失败，已停止重试：{detail}。请人工处理。",
                state=work, tool_calls=tool_calls, status="give_up")

    return AgentResult(  # 护栏 1：轮数用尽
        reply="达到最大轮数仍未完成。已确认的信息：" + _known(work) + "；请补充后重试。",
        state=work, tool_calls=tool_calls, status="need_more_info")

# state 会话状态
def _decide(user_text: str, registry: ToolRegistry, history: list[str],
            trace_id: str, round_no: int, state: dict[str, Any]) -> Decision | None:
    """问模型要一个决策；解析失败带着错误原文重试一次，再失败返回 None。"""
    system = _system_for(registry)
    user = _user_for(user_text, history, state)
    feedback = ""
    for attempt in (1, 2):
        raw = complete_json(system,user + feedback,Decision.model_json_schema())
        try:
            decision = Decision.model_validate(raw)
        except pydantic.ValidationError as exc:
            feedback = (f"\n\n上次输出不合法，错误如下：\n{exc}\n"
                        "请严格按 JSON Schema 重新输出，不要加解释文字。")
            record(trace_id, STEP, {"round": round_no, "attempt": attempt,
                                    "ok": False, "raw": raw, "validation_error": str(exc)})
            continue
        record(trace_id, STEP, {"round": round_no, "attempt": attempt, "ok": True,
                                "action": decision.action, "thought": decision.thought})
        return decision
    return None

def _system_for(registry: ToolRegistry) -> str:
    """稳定前缀：角色 + 规则 + 工具清单（同一份 schema，模型看到的就是校验用的那份）。"""
    return BASE_SYSTEM + "\n\n可用工具（JSON）：\n" + json.dumps(
        registry.schemas(), ensure_ascii=False, indent=2)

def _user_for(user_text: str, history: list[str], state: dict[str, Any]) -> str:
    parts = ["用户本轮输入：\n" + user_text]
    if state.get("intent"):
        parts.append("已确认的意图：\n" + _compact(state["intent"]))
    if history:
        parts.append("此前的工具观察：\n" + "\n".join(history))
    return "\n\n".join(parts)

def _observation(result: dict[str, Any]) -> str:
    """护栏 3：观察结果截断，并显式标注 —— 模型要知道自己看到的是残缺的。"""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    return text[:MAX_OBSERVATION_CHARS] + f"…[已截断，原始长度 {len(text)} 字符]"

def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:300]

def _final_status(reply: str, declared: bool) -> str:
    """反问 = final 的一种。

    判定两重：① 模型显式声明 need_more_info=True；② 兜底——以问号收尾也算。
    为什么要兜底：实测模型常把问句写成"请补充成本中心，例如 CC-01。"（句号结尾），只看问号会误判成 ok。
    """
    return "need_more_info" if (declared or reply.endswith(("?", "？"))) else "ok"

def _known(state: dict[str, Any]) -> str:
    keys = {k: v for k, v in state.items() if k not in ("trace_id", "session_id")}
    return _compact(keys) if keys else "（无）"
