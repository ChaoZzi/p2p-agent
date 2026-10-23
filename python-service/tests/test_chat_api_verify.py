"""【独立验证】web/chat_api.py + main.py —— 由 Hermes 编写，不是实现者自测。

真源：手抄自 docs/task-cards/P0-02-dsh-任务卡-chat_api.md §10「Hermes 会写的验收断言」
（10 条）+ §3 契约 + §9 自检清单。期望值都写在本文件顶部常量里；不从实现反推，
实现改坏了这里会直接指出是哪一条。

覆盖：
  A 契约形状：恰好 5 字段 / status 三态 / give_up 仍 200 / 非法 status 被拦成 500
  B 会话：首轮生成 + 回显、续用、turn 递增、未知 id 当新会话、副本隔离、上限 FIFO 淘汰
  C 400 口径：空串 / 全空格 / 缺字段 / 类型错 / 非法 JSON —— 且都不调用 agent.run
  D 500 口径：run 抛异常 → INTERNAL，响应里不含异常原文、不含堆栈
  E 装配：两个工具 schema、side_effect、request_id 幂等、trace_id 全程一致
  F 真链路：**真 agent + 真 registry**（替身 LLM / 替身 create_flow）——
    两轮跨 HTTP 请求把第 1 轮的 intent 接到第 2 轮，trace 文件里有同一个 trace_id
  G 执行器边界：抽失败不编造、缺 trace_id 报明确错误
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

import harness.agent as agent_mod
import web.chat_api as chat_api
from agents.intent import PurchaseIntent
from harness.agent import AgentResult
from main import create_app

RESPONSE_FIELDS = {"reply", "status", "session_id", "trace_id", "tool_calls"}
STATUS_VALUES = {"ok", "need_more_info", "give_up"}
TOOL_NAMES = {"extract_intent", "create_purchase_flow"}
CREATE_FLOW_REQUIRED = {"item", "qty", "reason", "cost_center"}


# ===========================================================================
# 夹具：每个测试一个干净的内存会话表 + 独立 trace 目录（不打进仓库）
# ===========================================================================
@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    chat_api._sessions.clear()
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / "trace"))
    yield
    chat_api._sessions.clear()


@pytest.fixture
def client():
    return TestClient(create_app())


# ===========================================================================
# 替身：agent.run（不联网、不花 LLM）
# ===========================================================================
def _fake_run(reply: str = "好的", status: str = "ok", tool_calls=None, recorder=None):
    def fake(user_text, *, state, trace_id, registry, max_rounds=agent_mod.MAX_ROUNDS):
        if recorder is not None:
            recorder.append({"user_text": user_text, "state": dict(state), "trace_id": trace_id})
        new_state = dict(state)
        return AgentResult(
            reply=reply,
            status=status,
            state=new_state,
            tool_calls=list(tool_calls or []),
        )

    return fake


class _ScriptedLLM:
    """按顺序吐出预设决策；记录每次 prompt（用来断言跨轮承接，而不是只看库里）。"""

    def __init__(self, decisions: list[dict]):
        self.queue = list(decisions)
        self.calls: list[dict] = []

    def __call__(self, system, user, schema, **kwargs):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.queue:
            raise AssertionError("scripted LLM ran out of decisions (unexpected extra round)")
        return self.queue.pop(0)


# ===========================================================================
# A. 契约形状（断言 1 / 5）
# ===========================================================================
def test_contract_exactly_five_fields(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run(status="ok"))
    resp = client.post("/api/chat", json={"message": "买一台笔记本"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == RESPONSE_FIELDS, f"response fields drifted: {sorted(body)}"
    assert body["status"] in STATUS_VALUES
    assert isinstance(body["tool_calls"], list)


def test_give_up_is_business_failure_not_http_failure(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run(reply="转人工", status="give_up"))
    resp = client.post("/api/chat", json={"message": "随便"})

    assert resp.status_code == 200, "give_up must stay 200 (business failure != HTTP failure)"
    assert resp.json()["status"] == "give_up"


def test_invalid_status_from_agent_becomes_500(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run(status="weird_fourth_state"))
    resp = client.post("/api/chat", json={"message": "随便"})

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL"


# ===========================================================================
# B. 会话（断言 2 / 7）+ 存储边界
# ===========================================================================
def test_first_turn_generates_and_echoes_session_id(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run())
    first = client.post("/api/chat", json={"message": "第一句"}).json()
    second = client.post("/api/chat", json={"message": "第二句"}).json()

    assert first["session_id"].startswith("s-")
    assert len(first["session_id"]) == len("s-") + 8
    assert first["session_id"] != second["session_id"], "each stateless request must get its own session"


def test_client_supplied_session_id_is_reused(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run())
    resp = client.post("/api/chat", json={"message": "继续", "session_id": "s-fixed-01"})

    assert resp.json()["session_id"] == "s-fixed-01"


def test_unknown_session_id_is_treated_as_new_not_error(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run())
    resp = client.post("/api/chat", json={"message": "你好", "session_id": "s-never-seen"})

    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s-never-seen"


def test_state_carries_session_id_and_incrementing_turn(client, monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(chat_api, "run", _fake_run(recorder=captured))

    first = client.post("/api/chat", json={"message": "一"}).json()
    sid = first["session_id"]
    client.post("/api/chat", json={"message": "二", "session_id": sid})
    client.post("/api/chat", json={"message": "三", "session_id": sid})

    assert [c["state"]["turn"] for c in captured] == [1, 2, 3]
    assert all(c["state"]["session_id"] == sid for c in captured)


def test_take_state_returns_a_copy(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run", _fake_run())
    sid = client.post("/api/chat", json={"message": "一"}).json()["session_id"]

    snapshot = chat_api._take_state(sid)
    snapshot["injected"] = "should not leak back"

    assert "injected" not in chat_api._sessions[sid], "_take_state must hand out a copy"


def test_session_store_evicts_oldest_beyond_limit(monkeypatch):
    monkeypatch.setattr(chat_api, "MAX_SESSIONS", 3)
    for index in range(5):
        chat_api._put_state(f"s-{index}", {"session_id": f"s-{index}"})

    assert len(chat_api._sessions) == 3
    assert set(chat_api._sessions) == {"s-2", "s-3", "s-4"}, "eviction must be insertion-order FIFO"


def test_concurrent_sessions_do_not_bleed(client, monkeypatch):
    def fake(user_text, *, state, trace_id, registry, max_rounds=agent_mod.MAX_ROUNDS):
        new_state = dict(state)
        new_state["intent"] = {"who": user_text}  # 模拟 agent 把意图写进 state
        return AgentResult(reply=user_text, status="ok", state=new_state)

    monkeypatch.setattr(chat_api, "run", fake)

    def run_session(name: str) -> str:
        sid = f"s-{name}"
        for turn in range(3):
            resp = client.post("/api/chat", json={"message": f"{name}-{turn}", "session_id": sid})
            assert resp.status_code == 200
        return sid

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run_session, ["A", "B"]))

    assert chat_api._sessions[a]["intent"]["who"] == "A-2"
    assert chat_api._sessions[b]["intent"]["who"] == "B-2"
    assert chat_api._sessions[a]["session_id"] == a


# ===========================================================================
# C. 400 口径（断言 4）：错误一律 INVALID_REQUEST，且一次 agent.run 都不该发生
# ===========================================================================
@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "\n\t "},
        {},
        {"session_id": "s-1"},
        {"message": ["not", "a", "string"]},
    ],
    ids=["empty", "spaces", "whitespace", "missing-message", "only-session", "wrong-type"],
)
def test_bad_message_returns_400_without_calling_agent(client, monkeypatch, payload):
    calls: list[dict] = []
    monkeypatch.setattr(chat_api, "run", _fake_run(recorder=calls))

    resp = client.post("/api/chat", json=payload)

    assert resp.status_code == 400, f"expected contract 400, got {resp.status_code}: {resp.text}"
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    assert calls == [], "validation must reject before spending an LLM call"


@pytest.mark.parametrize(
    "raw_body",
    [b"{not json", b"", b'{"message": }', b"[1,2,3]"],
    ids=["broken-json", "empty-body", "truncated", "json-array"],
)
def test_invalid_json_body_returns_400_not_422(client, monkeypatch, raw_body):
    calls: list[dict] = []
    monkeypatch.setattr(chat_api, "run", _fake_run(recorder=calls))

    resp = client.post("/api/chat", content=raw_body, headers={"Content-Type": "application/json"})

    assert resp.status_code == 400, "contract wants 400 INVALID_REQUEST, not FastAPI's 422"
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    assert calls == []


def test_rejection_is_logged_but_start_is_not(client, monkeypatch, caplog):
    monkeypatch.setattr(chat_api, "run", _fake_run())
    caplog.set_level("INFO", logger="chat_api")

    client.post("/api/chat", json={"message": ""})

    assert "chat rejected" in caplog.text
    assert "chat start" not in caplog.text, "no LLM should have been started"


# ===========================================================================
# D. 500 口径（断言 10）
# ===========================================================================
def test_internal_error_hides_details(client, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("BOOM-SECRET-DETAIL: db password is hunter2")

    monkeypatch.setattr(chat_api, "run", boom)
    caplog.set_level("INFO", logger="chat_api")

    resp = client.post("/api/chat", json={"message": "买点东西"})

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL"
    assert "BOOM-SECRET-DETAIL" not in resp.text, "exception text must not leak to clients"
    assert "hunter2" not in resp.text
    assert "Traceback" not in resp.text
    assert "chat internal error" in caplog.text, "server side must still keep the stack for debugging"


# ===========================================================================
# E. 装配（断言 6 / 8 / 9）
# ===========================================================================
def test_registry_schema_contract(client):
    schemas = {item["name"]: item for item in chat_api.REGISTRY.schemas()}

    assert set(schemas) == TOOL_NAMES

    extract = schemas["extract_intent"]
    assert set(extract["parameters"]["required"]) == {"text"}
    assert extract["parameters"]["properties"]["text"]["type"] == "string"

    create = schemas["create_purchase_flow"]
    assert set(create["parameters"]["required"]) == CREATE_FLOW_REQUIRED
    qty = create["parameters"]["properties"]["qty"]
    assert (qty["type"], qty["minimum"], qty["maximum"]) == ("integer", 1, 100)

    assert chat_api.REGISTRY.get("create_purchase_flow").side_effect is True
    assert chat_api.REGISTRY.get("extract_intent").side_effect is False
    assert all(item["description"].strip() for item in schemas.values())


def test_request_id_is_deterministic_per_business_identity(monkeypatch):
    seen: list[dict] = []

    def fake_create_flow(**kwargs):
        seen.append(kwargs)
        return {"flow_id": "req-fake", "state": "PENDING_APPROVAL"}

    monkeypatch.setattr(chat_api, "create_flow", fake_create_flow)
    fields = dict(item="MacBook Pro", qty=1, reason="给新来的前端用", cost_center="CC-01")

    chat_api._exec_create_purchase_flow(state={"session_id": "s-1", "trace_id": "t-1"}, **fields)
    chat_api._exec_create_purchase_flow(state={"session_id": "s-1", "trace_id": "t-2"}, **fields)
    chat_api._exec_create_purchase_flow(state={"session_id": "s-2", "trace_id": "t-3"}, **fields)

    assert seen[0]["request_id"] == seen[1]["request_id"], "same business identity must retry the same order"
    assert seen[0]["request_id"] != seen[2]["request_id"], "different session must not collide"
    assert seen[0]["request_id"].startswith("req-")
    assert len(seen[0]["request_id"]) == len("req-") + 8

    expected = "req-" + hashlib.sha1(
        "s-1|MacBook Pro|1|给新来的前端用|CC-01".encode()
    ).hexdigest()[:8]
    assert seen[0]["request_id"] == expected, "request_id recipe (session|item|qty|reason|cost_center) changed"


def test_trace_id_is_fresh_per_request_and_forwarded(client, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(chat_api, "run", _fake_run(recorder=calls))

    first = client.post("/api/chat", json={"message": "一"}).json()
    second = client.post("/api/chat", json={"message": "二"}).json()

    assert first["trace_id"] != second["trace_id"]
    assert [c["trace_id"] for c in calls] == [first["trace_id"], second["trace_id"]]


def test_create_flow_receives_the_request_trace_id(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(chat_api, "create_flow", lambda **kw: seen.append(kw) or {"flow_id": "req-x"})

    trace_id = "3f2a6c1e-9b44-4d7a-8c21-acde00000042"
    chat_api._exec_create_purchase_flow(
        state={"session_id": "s-42", "trace_id": trace_id},
        item="显示器", qty=2, reason="工位升级", cost_center="CC-01",
    )

    assert seen[0]["trace_id"] == trace_id, "the id must reach Java unchanged (that is the join key)"


# ===========================================================================
# F. 真链路：真 agent + 真 registry，替身 LLM / 替身 Java 建单（断言 3 / 6 的落地版）
# ===========================================================================
def test_two_turns_over_http_carry_intent_and_trace(client, monkeypatch, tmp_path):
    llm = _ScriptedLLM(
        [
            {  # 第 1 轮 · 第 1 步：抽意图
                "thought": "先抽结构化意图",
                "action": "tool:extract_intent",
                "args": {"text": "我想申请采购 1 台 MacBook Pro，给新来的前端用"},
            },
            {  # 第 1 轮 · 第 2 步：缺成本中心 → 反问
                "thought": "还差成本中心",
                "action": "final",
                "args": {},
                "reply": "请补充成本中心，例如 CC-01。",
                "need_more_info": True,
            },
            {  # 第 2 轮 · 第 1 步：真的建单
                "thought": "信息齐了",
                "action": "tool:create_purchase_flow",
                "args": {
                    "item": "MacBook Pro",
                    "qty": 1,
                    "reason": "给新来的前端用",
                    "cost_center": "CC-01",
                },
            },
            {  # 第 2 轮 · 第 2 步：收尾
                "thought": "完成",
                "action": "final",
                "args": {},
                "reply": "已创建采购单，等待审批。",
            },
        ]
    )
    monkeypatch.setattr(agent_mod, "complete_json", llm)
    monkeypatch.setattr(
        chat_api,
        "extract",
        lambda text, trace_id: (
            PurchaseIntent(item="MacBook Pro", qty=1, reason="给新来的前端用"),
            ["cost_center"],
        ),
    )
    created: list[dict] = []

    def fake_create_flow(**kwargs):
        created.append(kwargs)
        return {"flow_id": "req-real-1", "state": "PENDING_APPROVAL"}

    monkeypatch.setattr(chat_api, "create_flow", fake_create_flow)

    first = client.post(
        "/api/chat", json={"message": "我想申请采购 1 台 MacBook Pro，给新来的前端用"}
    )
    body1 = first.json()
    assert first.status_code == 200
    assert body1["status"] == "need_more_info", "a反问 must be need_more_info, not ok"
    assert [call["tool"] for call in body1["tool_calls"]] == ["extract_intent"]

    session_id = body1["session_id"]
    stored = chat_api._sessions[session_id]
    assert stored["intent"]["item"] == "MacBook Pro", "intent must be persisted for the next HTTP request"
    assert stored["intent"]["cost_center"] is None
    assert stored["turn"] == 1

    second = client.post(
        "/api/chat", json={"session_id": session_id, "message": "成本中心填 CC-01"}
    )
    body2 = second.json()
    assert second.status_code == 200
    assert body2["status"] == "ok"
    assert [call["tool"] for call in body2["tool_calls"]] == ["create_purchase_flow"]
    assert body1["trace_id"] != body2["trace_id"], "each turn is its own trace"

    # 跨请求承接的证据：第 2 轮喂给模型的 prompt 里带着第 1 轮抽到的意图
    second_turn_prompt = llm.calls[2]["user"]
    assert "已确认的意图" in second_turn_prompt
    assert "MacBook Pro" in second_turn_prompt
    assert "成本中心填 CC-01" in second_turn_prompt

    assert len(created) == 1
    assert created[0]["cost_center"] == "CC-01"
    assert created[0]["trace_id"] == body2["trace_id"], "Java must see the same trace id as the HTTP response"

    trace_file = tmp_path / "trace" / f"{body2['trace_id']}.jsonl"
    assert trace_file.is_file(), "trace file must be written for the response trace_id"
    entries = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries and all(entry["trace_id"] == body2["trace_id"] for entry in entries)
    assert any(entry["step"] == "tool.invoke" for entry in entries)

    assert chat_api._sessions[session_id]["turn"] == 2


# ===========================================================================
# G. 执行器边界
# ===========================================================================
def test_extract_executor_does_not_invent_intent(monkeypatch):
    monkeypatch.setattr(chat_api, "extract", lambda text, trace_id: (None, []))

    result = chat_api._exec_extract_intent(state={"trace_id": "t"}, text="乱七八糟")

    assert result["intent"] is None, "a failed extraction must stay None, never a default object"
    assert result.get("note"), "the model needs to be told the extraction failed"
    assert "missing" in result


def test_create_flow_executor_reports_missing_trace_id():
    with pytest.raises(RuntimeError) as excinfo:
        chat_api._exec_create_purchase_flow(
            state={"session_id": "s-1"}, item="鼠标", qty=1, reason="替换坏的", cost_center="CC-01"
        )

    assert "trace_id" in str(excinfo.value), "error must name the missing key, not just raise KeyError"


def test_extract_executor_passes_state_trace_id(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_extract(text, trace_id):
        seen.append((text, trace_id))
        return PurchaseIntent(item="键盘", qty=1, reason="坏了"), []

    monkeypatch.setattr(chat_api, "extract", fake_extract)

    result = chat_api._exec_extract_intent(state={"trace_id": "t-42"}, text="买个键盘")

    assert seen == [("买个键盘", "t-42")]
    assert result["intent"]["item"] == "键盘"
