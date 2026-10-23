"""【独立验证】web/flow_proxy.py + 演示页静态路由 —— 由 Hermes 编写。

真源：`docs/architecture.md` §2.2（Python :8001 透传 `GET /api/flows/{id}` + 静态演示页 `/`）、
`tools/flow_client.py` 模块 docstring 里的契约（approve 要 X-Operator、reject 要 body.reason、
非 2xx 抛 ContractError、trace 必须原样回显），以及页面实际用到的三条数据流：
建单 → 按 request_id 反查 flow_id → 审批 + 读审计。

覆盖：
  A 演示页本身：GET / 返回 HTML（不是 JSON、不是 404）
  B by-request：命中返回 FlowView，未命中 404 REQUEST_NOT_FOUND
  C 透传：GET / GET audit / POST approve / POST reject 都打到 flow_client，且 header 正确
  D 错误口径：ContractError 原样透出 status/code、trace 断链 502、Java 不可达 503
  E backend-status：Java 可达 → ok，连不上 → down（页面顶灯靠它）
  F 副作用：chat_api 建单后写 FLOW_BY_REQUEST（页面靠这张表拿 flow_id）
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import web.flow_proxy as proxy
from main import create_app
from tools.flow_client import ContractError, TracePropagationError
from web import chat_api


@pytest.fixture(autouse=True)
def clean_index():
    chat_api.FLOW_BY_REQUEST.clear()
    yield
    chat_api.FLOW_BY_REQUEST.clear()


@pytest.fixture
def client():
    return TestClient(create_app())


FLOW = {
    "id": "96d21946-b3b9-4254-88aa-61cefe6d5961",
    "request_id": "req-5a0cc491",
    "state": "PENDING_APPROVAL",
    "item": "MacBook Pro",
    "qty": 1,
    "reason": "给新来的前端用",
    "cost_center": "CC-01",
    "created_at": "2026-10-23T02:37:18.809348Z",
    "updated_at": "2026-10-23T02:37:18.815343200Z",
}


# ===========================================================================
# A. 演示页本体
# ===========================================================================
def test_index_serves_the_page(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    for marker in ("P2P", "/api/chat", "/api/flows", "timeline"):
        assert marker in body, f"page is missing {marker!r}"


# ===========================================================================
# B. by-request（页面唯一的 flow_id 来源）
# ===========================================================================
def test_by_request_returns_indexed_flow(client):
    chat_api.FLOW_BY_REQUEST[FLOW["request_id"]] = FLOW

    resp = client.get(f"/api/flows/by-request/{FLOW['request_id']}")

    assert resp.status_code == 200
    assert resp.json()["id"] == FLOW["id"]


def test_by_request_unknown_returns_structured_404(client):
    resp = client.get("/api/flows/by-request/req-00000000")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "REQUEST_NOT_FOUND"


def test_by_request_does_not_shadow_flow_lookup(client, monkeypatch):
    """`/by-request/...` 与 `/{flow_id}` 同前缀，顺序错了会互相吞。"""
    seen: list[str] = []
    monkeypatch.setattr(proxy.flow_client, "get_flow", lambda flow_id, *, trace_id: seen.append(flow_id) or FLOW)

    assert client.get("/api/flows/by-request/req-5a0cc491").status_code == 404  # 索引为空 → 404，而不是打到 Java
    assert client.get("/api/flows/flow-abc").status_code == 200
    assert seen == ["flow-abc"]


# ===========================================================================
# C. 透传：方法 / 参数 / header
# ===========================================================================
def test_get_flow_passthrough(client, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        proxy.flow_client, "get_flow",
        lambda flow_id, *, trace_id: calls.append((flow_id, trace_id)) or FLOW,
    )

    resp = client.get(f"/api/flows/{FLOW['id']}")

    assert resp.status_code == 200 and resp.json()["state"] == "PENDING_APPROVAL"
    assert calls and calls[0][0] == FLOW["id"]
    assert len(calls[0][1]) == 36, "proxy must mint a trace_id per call"


def test_audit_passthrough(client, monkeypatch):
    rows = [{"audit_id": "a1", "action": "FLOW_CREATED", "actor": "system",
             "detail": None, "trace_id": "t-1", "at": "2026-10-23T02:37:18.812Z", "flow_id": FLOW["id"]}]
    monkeypatch.setattr(proxy.flow_client, "get_audit", lambda flow_id, *, trace_id: rows)

    resp = client.get(f"/api/flows/{FLOW['id']}/audit")

    assert resp.status_code == 200
    assert [row["action"] for row in resp.json()] == ["FLOW_CREATED"]


def test_approve_forwards_operator(client, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        proxy.flow_client, "approve",
        lambda flow_id, *, operator, trace_id: seen.append({"id": flow_id, "op": operator}) or {**FLOW, "state": "COMPLETED"},
    )

    resp = client.post(f"/api/flows/{FLOW['id']}/approve", headers={"X-Operator": "alice"})

    assert resp.status_code == 200 and resp.json()["state"] == "COMPLETED"
    assert seen == [{"id": FLOW["id"], "op": "alice"}]


def test_reject_requires_reason(client, monkeypatch):
    called = []
    monkeypatch.setattr(proxy.flow_client, "reject", lambda *a, **k: called.append(1) or FLOW)

    bad = client.post(f"/api/flows/{FLOW['id']}/reject", json={"reason": "   "})
    good = client.post(f"/api/flows/{FLOW['id']}/reject", json={"reason": "预算超了"},
                       headers={"X-Operator": "bob"})

    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_REQUEST"
    assert called == [1], "empty reason must not reach Java"
    assert good.status_code == 200


# ===========================================================================
# D. 错误口径
# ===========================================================================
def test_contract_error_status_and_code_are_preserved(client, monkeypatch):
    def conflict(flow_id, *, trace_id):
        raise ContractError(409, "FLOW_STATE_CONFLICT", "cannot transit COMPLETED -> APPROVED", "t-9", f"/api/flows/{flow_id}")

    monkeypatch.setattr(proxy.flow_client, "get_flow", conflict)

    resp = client.get(f"/api/flows/{FLOW['id']}")

    assert resp.status_code == 409, "页面要看到 Java 的真实状态码，不能一律 500"
    body = resp.json()["error"]
    assert body["code"] == "FLOW_STATE_CONFLICT"
    assert "COMPLETED" in body["message"]


def test_trace_break_is_502(client, monkeypatch):
    def broken(flow_id, *, trace_id):
        raise TracePropagationError("trace_id 未原样回显")

    monkeypatch.setattr(proxy.flow_client, "get_flow", broken)

    resp = client.get(f"/api/flows/{FLOW['id']}")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "TRACE_PROPAGATION"


def test_java_unreachable_is_503(client, monkeypatch):
    def down(flow_id, *, trace_id):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(proxy.flow_client, "get_flow", down)

    resp = client.get(f"/api/flows/{FLOW['id']}")

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "JAVA_UNAVAILABLE"


# ===========================================================================
# E. 顶灯
# ===========================================================================
def test_backend_status_ok(client, monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            return httpx.Response(200, json=FLOW, request=httpx.Request("GET", "http://127.0.0.1:8000"))

    monkeypatch.setattr(proxy.httpx, "Client", FakeClient)

    assert client.get("/api/flows/backend-status").json()["java"] == "ok"


def test_backend_status_down(client, monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(proxy.httpx, "Client", BoomClient)

    body = client.get("/api/flows/backend-status").json()

    assert body["java"] == "down"


# ===========================================================================
# F. 建单 → 索引（页面据此拿 flow_id）
# ===========================================================================
def test_create_flow_executor_indexes_request_id(monkeypatch):
    def fake_create_flow(**kwargs):
        return {**FLOW, "request_id": kwargs["request_id"]}

    monkeypatch.setattr(chat_api, "create_flow", fake_create_flow)

    result = chat_api._exec_create_purchase_flow(
        state={"session_id": "s-1", "trace_id": "t-1"},
        item="MacBook Pro", qty=1, reason="给新来的前端用", cost_center="CC-01",
    )

    assert chat_api.FLOW_BY_REQUEST[result["request_id"]]["id"] == FLOW["id"]
    assert result["request_id"].startswith("req-")
