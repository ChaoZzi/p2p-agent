"""【独立验证】harness/agent.py —— 由 Hermes 编写，不是实现者自测。

真源：手抄自 docs/task-cards/P0-02-你敲规格-Python.md §3（职责 / 规格签名 / 四道护栏 /
三态 status / 8 种场景表）与 §4（跨轮承接）。**不读实现反推行为**：期望值都写在本文件
顶部的 SPEC_* 常量里，改坏了测试立刻指出是哪一条。

覆盖：
  ① 结构层：常量值、run() 的关键字参数、AgentResult 四个字段
  ② 单轮 final：0 次工具调用、status=ok
  ③ 工具 → 反问：第 2 轮 prompt 里带回 `[round 1]` 观察；status=need_more_info
  ④ 反问兜底：句号结尾 + need_more_info=true 也算反问（只看问号会误判）
  ⑤ 护栏：决策两次都不合法 → give_up（恰好问 2 次）
  ⑥ 护栏：同一工具连续失败 2 次 → 立刻收口
  ⑦ 护栏：轮数用尽 → need_more_info（提示里带已确认信息）
  ⑧ 护栏：观察超长截断并显式标注
  ⑨ 契约：调用方 state 不被改；返回的 state 带 trace_id
  ⑩ **跨轮承接**：extract_intent 成功后 intent 留在 state 里 —— 下一轮 prompt 含
     `已确认的意图`，且 chat_api 落库后下一个 HTTP 请求还接得上（这是 2026-10-22 补的回归用例）
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path

import pytest

import harness.agent as agent_mod

# ── 真源（手抄规格 §3，不来自实现）──────────────────────────────
SPEC_MAX_ROUNDS = 3                 # 护栏 1：轮数上限
SPEC_MAX_CONSECUTIVE_FAILURES = 2   # 护栏 2：同一工具连续失败上限
SPEC_MAX_OBSERVATION_CHARS = 2000   # 护栏 3：观察截断阈值
SPEC_RECORD_STEP = "agent.run"      # 行为：落 trace 的步骤名
SPEC_TOOL_PREFIX = "tool:"
SPEC_STATUSES = {"ok", "need_more_info", "give_up"}

SPEC_INTENT = {"item": "MacBook Pro", "qty": 1, "reason": "新来的前端同事用"}
SPEC_MISSING = ["cost_center"]
SPEC_TURN1 = "我想申请采购 1 台 MacBook Pro，给新来的前端用"
SPEC_TURN2 = "成本中心填 CC-01"


# ── 替身与夹具 ────────────────────────────────────────────────

class FakeLLM:
    """替身：按脚本逐次返回决策，并记录每次收到的 (system, user, schema)。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, system, user, schema, **kwargs):
        self.calls.append({"system": system, "user": user, "schema": schema, "kwargs": kwargs})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return dict(self.responses[index])


@pytest.fixture()
def trace_dir(tmp_path, monkeypatch):
    """trace 落到临时目录，别污染仓库的 trace/。"""
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    return tmp_path


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr(agent_mod, "complete_json", fake)
    return fake


def _trace_lines(trace_dir: Path, trace_id: str) -> list[dict]:
    path = trace_dir / f"{trace_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _registry(*tools):
    """造一个真 ToolRegistry（走真 registry 的查表 → 校验 → 执行 → 记录四步）。"""
    from harness.tool_registry import Tool, ToolRegistry

    registry = ToolRegistry()
    for name, executor, parameters, side_effect in tools:
        registry.register(Tool(name=name, description=f"test tool {name}",
                               parameters=parameters, executor=executor,
                               side_effect=side_effect))
    return registry


def _intent_tool(record: dict | None = None, *, raises: Exception | None = None):
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1}},
        "required": ["text"],
    }

    def executor(state=None, **kwargs):
        if record is not None:
            record.update(kwargs)
        if raises is not None:
            raise raises
        return {"intent": dict(SPEC_INTENT), "missing": list(SPEC_MISSING)}

    return ("extract_intent", executor, schema, False)


def _flow_tool(record: dict | None = None):
    schema = {
        "type": "object",
        "properties": {"item": {"type": "string", "minLength": 1},
                       "qty": {"type": "integer", "minimum": 1, "maximum": 100},
                       "reason": {"type": "string", "minLength": 1},
                       "cost_center": {"type": "string", "minLength": 1}},
        "required": ["item", "qty", "reason", "cost_center"],
    }

    def executor(state=None, **kwargs):
        if record is not None:
            record.update(kwargs)
        return {"flow_id": "cb0b3daa-0000-4000-8000-000000000001",
                "state": "PENDING_APPROVAL"}

    return ("create_purchase_flow", executor, schema, True)


def _new_trace_id() -> str:
    return str(uuid.uuid4())


def _final(reply: str, *, need_more_info: bool = False) -> dict:
    return {"thought": "收尾", "action": "final", "args": {},
            "reply": reply, "need_more_info": need_more_info}


def _call(tool: str, **args) -> dict:
    return {"thought": "调工具", "action": f"tool:{tool}", "args": args}


# ─────────────────────────────────────── ① 结构层

def test_constants_match_spec():
    assert agent_mod.MAX_ROUNDS == SPEC_MAX_ROUNDS, "护栏 1 的轮数上限被改了"
    assert agent_mod.MAX_CONSECUTIVE_FAILURES == SPEC_MAX_CONSECUTIVE_FAILURES, "护栏 2 的失败上限被改了"
    assert agent_mod.MAX_OBSERVATION_CHARS == SPEC_MAX_OBSERVATION_CHARS, "护栏 3 的截断阈值被改了"
    assert agent_mod.STEP == SPEC_RECORD_STEP, "trace 步骤名必须与规格一致（grep 靠它）"
    assert agent_mod.TOOL_PREFIX == SPEC_TOOL_PREFIX
    assert agent_mod.MAX_ROUNDS == 3, "规格 §3 钉死 3 轮（再多说明任务超出模型能力）"


def test_run_signature_is_keyword_only_after_user_text():
    params = list(inspect.signature(agent_mod.run).parameters)
    assert params == ["user_text", "state", "trace_id", "registry", "max_rounds"], (
        f"run() 的入参必须与规格 §3 一致，实际 {params}；"
        "state/trace_id/registry 必须 keyword-only（位置传参迟早传错）"
    )
    kinds = {name: p.kind for name, p in inspect.signature(agent_mod.run).parameters.items()}
    for name in ("state", "trace_id", "registry", "max_rounds"):
        assert kinds[name] is inspect.Parameter.KEYWORD_ONLY, f"{name} 必须是 keyword-only"
    assert inspect.signature(agent_mod.run).parameters["max_rounds"].default == SPEC_MAX_ROUNDS


def test_agent_result_fields():
    from dataclasses import fields

    names = [f.name for f in fields(agent_mod.AgentResult)]
    assert names == ["reply", "state", "tool_calls", "status"], f"AgentResult 字段变了：{names}"
    result = agent_mod.AgentResult(reply="x", state={})
    assert result.tool_calls == [] and result.status == "ok", "tool_calls 默认 []、status 默认 ok"


# ─────────────────────────────────────── ② 单轮 final

def test_single_final_round_uses_no_tool(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    fake = _patch_llm(monkeypatch, FakeLLM(_final("需要我做什么？", need_more_info=True)))
    trace_id = _new_trace_id()

    result = agent_mod.run(SPEC_TURN1, state={"session_id": "s-42", "turn": 1},
                           trace_id=trace_id, registry=registry)

    assert result.status == "need_more_info"
    assert result.tool_calls == [], "第一轮就 final 就不该有任何工具调用"
    assert len(fake.calls) == 1
    assert "可用工具（JSON）" in fake.calls[0]["system"], "system 必须带工具清单"
    assert "extract_intent" in fake.calls[0]["user"] or "extract_intent" in fake.calls[0]["system"]
    steps = [x["payload"] for x in _trace_lines(trace_dir, trace_id) if x["step"] == SPEC_RECORD_STEP]
    assert [s["action"] for s in steps] == ["final"], f"trace 里应恰好 1 行 agent.run，实际 {steps}"


# ─────────────────────────────────────── ③ 工具 → 反问

def test_tool_observation_is_fed_into_next_round(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    fake = _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _final("请补充成本中心（cost_center），例如 CC-01。", need_more_info=True),
    ))

    result = agent_mod.run(SPEC_TURN1, state={"session_id": "s-42"},
                           trace_id=_new_trace_id(), registry=registry)

    assert result.status == "need_more_info"
    assert [c["tool"] for c in result.tool_calls] == ["extract_intent"]
    assert result.tool_calls[0]["ok"] is True and result.tool_calls[0]["round"] == 1
    assert len(fake.calls) == 2, "工具之后必须再问一次模型"
    second_prompt = fake.calls[1]["user"]
    assert "[round 1]" in second_prompt, "第 2 轮 prompt 必须回填工具观察（带轮次号）"
    assert "extract_intent" in second_prompt
    assert "MacBook Pro" in second_prompt, "观察内容要原样进 prompt（截断前）"


def test_question_mark_alone_is_need_more_info(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(_final("成本中心填什么？")))
    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)
    assert result.status == "need_more_info", "以问号收尾 = 反问（模型没给旗标也要兜住）"


def test_flag_overrides_period_ending(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(_final("请补充成本中心，例如 CC-01。", need_more_info=True)))
    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)
    assert result.status == "need_more_info", "句号结尾但 need_more_info=true 仍是反问（场景 H 反例）"


def test_plain_final_is_ok(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(_final("已建单，单号 cb0b3daa。")))
    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)
    assert result.status == "ok"


# ─────────────────────────────────────── ④/⑤ 护栏

def test_two_bad_decisions_give_up_after_exactly_two_calls(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    fake = _patch_llm(monkeypatch, FakeLLM({"action": ""}, {"action": ""}))
    trace_id = _new_trace_id()

    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=trace_id, registry=registry)

    assert result.status == "give_up"
    assert len(fake.calls) == 2, f"解析失败只重试一次，应恰好问 2 次，实际 {len(fake.calls)}"
    assert "已转人工" in result.reply
    bad = [x["payload"] for x in _trace_lines(trace_dir, trace_id)
           if x["step"] == SPEC_RECORD_STEP and x["payload"].get("ok") is False]
    assert len(bad) == 2 and all("validation_error" in b for b in bad), "失败轮必须落 trace 且带错误原文"


def test_same_tool_failing_twice_stops(monkeypatch, trace_dir):
    registry = _registry(_intent_tool(raises=RuntimeError("deepseek 挂了")))
    fake = _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _call("extract_intent", text=SPEC_TURN1),
        _final("不该走到这里"),
    ))

    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)

    assert result.status == "give_up"
    assert len(fake.calls) == 2, "第 2 次失败后必须立刻收口，不能再问第 3 次（烧钱）"
    assert [c["ok"] for c in result.tool_calls] == [False, False]
    assert "连续 2 次失败" in result.reply and "deepseek 挂了" in result.reply


def test_unknown_tool_does_not_crash(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    fake = _patch_llm(monkeypatch, FakeLLM(
        _call("create_purchase_flow", item="x"),          # 没注册的工具
        _call("create_purchase_flow", item="x"),
        _final("放弃"),
    ))
    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)
    assert result.status == "give_up"
    assert result.tool_calls[0]["ok"] is False, "未注册工具 = TOOL_NOT_FOUND，不许抛"
    assert len(fake.calls) == 2


def test_max_rounds_exhausted_is_need_more_info(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    fake = _patch_llm(monkeypatch, FakeLLM(_call("extract_intent", text=SPEC_TURN1)))

    result = agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)

    assert result.status == "need_more_info"
    assert len(fake.calls) == SPEC_MAX_ROUNDS, f"轮数用尽应恰好问 {SPEC_MAX_ROUNDS} 次，实际 {len(fake.calls)}"
    assert "达到最大轮数" in result.reply
    assert "MacBook Pro" in result.reply, "轮数用尽时要把已确认的信息回给用户（别让用户从头再说一遍）"


def test_observation_over_limit_is_truncated_and_marked(monkeypatch, trace_dir):
    huge = {"blob": "x" * 3000}
    registry = _registry(("extract_intent", lambda state=None, **kw: huge,
                          {"type": "object", "properties": {"text": {"type": "string"}}, "required": []},
                          False))
    fake = _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _final("好", need_more_info=True),
    ))

    agent_mod.run(SPEC_TURN1, state={}, trace_id=_new_trace_id(), registry=registry)

    second = fake.calls[1]["user"]
    assert "[已截断，原始长度" in second, "超长观察必须显式标注截断，模型要知道自己看的是残片"
    assert "x" * SPEC_MAX_OBSERVATION_CHARS not in second


# ─────────────────────────────────────── ⑨ state 契约

def test_caller_state_is_not_mutated(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(_call("extract_intent", text=SPEC_TURN1), _final("好", need_more_info=True)))
    caller_state = {"session_id": "s-42", "turn": 1}
    trace_id = _new_trace_id()

    result = agent_mod.run(SPEC_TURN1, state=caller_state, trace_id=trace_id, registry=registry)

    assert caller_state == {"session_id": "s-42", "turn": 1}, "调用方传进来的 dict 不许被改（要拷贝）"
    assert result.state is not caller_state
    assert result.state["trace_id"] == trace_id, "返回的 state 必须带 trace_id（chat_api 落库用）"
    assert "trace_id" not in caller_state


def test_status_is_always_one_of_three(monkeypatch, trace_dir):
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(_final("好")))
    result = agent_mod.run("hi", state=None, trace_id=_new_trace_id(), registry=registry)
    assert result.status in SPEC_STATUSES, "status 只能是 ok / need_more_info / give_up"
    assert result.state["trace_id"], "state=None 也要能跑（chat_api 首轮可能给空 dict）"


# ─────────────────────────────────────── ⑩ 跨轮承接（2026-10-22 回归用例）

def test_intent_is_kept_in_state_after_extract(monkeypatch, trace_dir):
    """第 1 轮：extract_intent 成功 → 反问。intent 必须留在返回的 state 里。"""
    registry = _registry(_intent_tool())
    _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _final("请补充成本中心（cost_center），例如 CC-01。", need_more_info=True),
    ))

    result = agent_mod.run(SPEC_TURN1, state={"session_id": "s-42", "turn": 1},
                           trace_id=_new_trace_id(), registry=registry)

    assert result.state.get("intent") == SPEC_INTENT, (
        "extract_intent 成功后 intent 必须写进 state："
        "① 本轮第 2 次问模型时 prompt 要靠它；② chat_api 要把 state 存回内存 dict 给下一个请求。"
        f"实际 state={result.state}"
    )


def test_second_turn_prompt_carries_confirmed_intent(monkeypatch, trace_dir):
    """第 2 轮（用户补答）：同一条链上跑两次 run()，第 2 次 prompt 必须带上第 1 轮的 intent。"""
    registry = _registry(_intent_tool(), _flow_tool())

    # 第 1 轮：抽意图 → 缺成本中心 → 反问
    _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _final("请补充成本中心（cost_center），例如 CC-01。", need_more_info=True),
    ))
    turn1 = agent_mod.run(SPEC_TURN1, state={"session_id": "s-42", "turn": 1},
                          trace_id=_new_trace_id(), registry=registry)
    assert turn1.status == "need_more_info"

    # chat_api 的动作：_sessions["s-42"] = turn1.state
    persisted = dict(turn1.state)

    # 第 2 轮：用户只说“成本中心填 CC-01”
    fake2 = _patch_llm(monkeypatch, FakeLLM(_final("已建单，单号 cb0b3daa-…，当前状态待审批。")))
    turn2 = agent_mod.run(SPEC_TURN2, state=persisted,
                          trace_id=_new_trace_id(), registry=registry)

    first_prompt_of_turn2 = fake2.calls[0]["user"]
    assert "已确认的意图" in first_prompt_of_turn2, (
        "第 2 轮 prompt 少了「已确认的意图」段 —— 用户只说了一句“成本中心填 CC-01”，"
        "模型看不到 item/qty/reason 就会再问一遍，甚至重新抽错。"
        f"实际 prompt={first_prompt_of_turn2!r}"
    )
    assert "MacBook Pro" in first_prompt_of_turn2 and "新来的前端同事用" in first_prompt_of_turn2
    assert SPEC_TURN2 in first_prompt_of_turn2, "用户本轮原话也要在"
    assert "此前的工具观察" not in first_prompt_of_turn2, "跨轮不该把上一轮的观察当历史（history 是本轮内的）"
    assert turn2.state.get("intent") == SPEC_INTENT, "承接过来的 intent 不许丢"


def test_second_turn_creates_flow_with_carried_intent(monkeypatch, trace_dir):
    """跨轮真实路径：第 2 轮直接建单，工具拿到的是第 1 轮抽出来的 item/qty/reason + 本轮的成本中心。"""
    seen: dict = {}
    registry = _registry(_intent_tool(), _flow_tool(record=seen))

    _patch_llm(monkeypatch, FakeLLM(
        _call("extract_intent", text=SPEC_TURN1),
        _final("请补充成本中心（cost_center），例如 CC-01。", need_more_info=True),
    ))
    turn1 = agent_mod.run(SPEC_TURN1, state={"session_id": "s-42", "turn": 1},
                          trace_id=_new_trace_id(), registry=registry)

    _patch_llm(monkeypatch, FakeLLM(
        _call("create_purchase_flow", item="MacBook Pro", qty=1,
              reason="新来的前端同事用", cost_center="CC-01"),
        _final("已建单，单号 cb0b3daa-…，当前状态待审批。"),
    ))
    turn2 = agent_mod.run(SPEC_TURN2, state=dict(turn1.state),
                          trace_id=_new_trace_id(), registry=registry)

    assert turn2.status == "ok"
    assert [c["tool"] for c in turn2.tool_calls] == ["create_purchase_flow"]
    assert seen == {"item": "MacBook Pro", "qty": 1,
                    "reason": "新来的前端同事用", "cost_center": "CC-01"}
    assert turn2.state.get("intent") == SPEC_INTENT, "建单后 intent 仍在 state 里（页侧栏要展示）"
