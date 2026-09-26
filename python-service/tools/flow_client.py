"""Java 流程端点客户端（P0-02 主体 A3）。

对接的契约（Java :8000，见 docs/contracts/java-service-openapi.json）：

    POST /api/flows                 body {request_id, item, qty, reason, cost_center}
    GET  /api/flows/{id}
    POST /api/flows/{id}/approve    header X-Operator
    POST /api/flows/{id}/reject     header X-Operator, body {reason}
    GET  /api/flows/{id}/audit

三条与本项目其他客户端一致的做法（沿用 tools/oa_client.py 的写法）：
  1. **trace_id 由调用方传入**（不是这里生成）：一次用户请求从头到尾只用一个 trace，
     Python 的 trace JSONL 与 Java 的日志/审计才可能对得上；
  2. **每次调用都断言 X-Trace-Id 原样回显** —— 不满足就抛 TracePropagationError，
     静默通过等于没验；
  3. **非 2xx 不返回半成品**，而是抛 ContractError，把 Java 的统一错误体
     （code / message / trace_id）原样带出来，让上层（agent loop）可以决策：
     409 是并发冲突、404 是单据不存在、422 是我参数写错了、501 是没实现。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx

TRACE_HEADER = "X-Trace-Id"
REQUEST_ID_HEADER = "X-Request-Id"
OPERATOR_HEADER = "X-Operator"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_FLOW_ID = "flow-seed-0001"


class ContractError(RuntimeError):
    """Java 侧返回了非 2xx（错误体是统一形状：{"error":{"code","message","trace_id"}}）。"""

    def __init__(self, status: int, code: str | None, message: str | None, trace_id: str | None, path: str):
        super().__init__(f"{path} -> HTTP {status} code={code} message={message}")
        self.status = status
        self.code = code
        self.message = message
        self.trace_id = trace_id
        self.path = path


class TracePropagationError(AssertionError):
    """trace_id 没有被 Java 原样回显 —— 透传链路断了。"""


def new_trace_id() -> str:
    """贯穿一次请求的 trace：HTTP 头 → Java 日志/审计 → Python trace JSONL。"""
    return str(uuid.uuid4())


def new_request_id(prefix: str = "req") -> str:
    """幂等键；同一逻辑动作重试时必须复用同一个（这才叫"重复请求"）。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _request(
    method: str,
    path: str,
    *,
    trace_id: str,
    body: dict | None = None,
    extra_headers: dict | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
) -> httpx.Response:
    headers = {TRACE_HEADER: trace_id}
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        response = client.request(method, path, json=body, headers=headers)

    echoed = response.headers.get(TRACE_HEADER)
    if echoed != trace_id:
        raise TracePropagationError(f"{path}: trace_id 未原样回显 sent={trace_id!r} echoed={echoed!r}")
    return response


def _payload(response: httpx.Response) -> object:
    if response.status_code // 100 != 2:
        code = message = trace = None
        try:
            error = (response.json() or {}).get("error") or {}
            code, message, trace = error.get("code"), error.get("message"), error.get("trace_id")
        except Exception:  # 错误体本身不是 JSON（例如网关返回的 HTML）
            message = response.text[:200]
        raise ContractError(response.status_code, code, message, trace, str(response.request.url.path))
    return response.json()


def create_flow(
    *,
    request_id: str,
    item: str,
    qty: int,
    reason: str,
    cost_center: str,
    trace_id: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
) -> dict:
    """建单（幂等：同 request_id 返回原单）。成功时 state 应为 PENDING_APPROVAL。"""
    response = _request(
        "POST",
        "/api/flows",
        trace_id=trace_id,
        body={
            "request_id": request_id,
            "item": item,
            "qty": qty,
            "reason": reason,
            "cost_center": cost_center,
        },
        extra_headers={REQUEST_ID_HEADER: request_id},
        base_url=base_url,
        timeout=timeout,
    )
    return _payload(response)  # type: ignore[return-value]


def get_flow(flow_id: str, *, trace_id: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> dict:
    return _payload(_request("GET", f"/api/flows/{flow_id}", trace_id=trace_id,
                             base_url=base_url, timeout=timeout))  # type: ignore[return-value]


def approve(flow_id: str, *, operator: str, trace_id: str,
            base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> dict:
    response = _request("POST", f"/api/flows/{flow_id}/approve", trace_id=trace_id,
                        extra_headers={OPERATOR_HEADER: operator}, base_url=base_url, timeout=timeout)
    return _payload(response)  # type: ignore[return-value]


def reject(flow_id: str, *, operator: str, reason: str, trace_id: str,
           base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> dict:
    response = _request("POST", f"/api/flows/{flow_id}/reject", trace_id=trace_id,
                        body={"reason": reason}, extra_headers={OPERATOR_HEADER: operator},
                        base_url=base_url, timeout=timeout)
    return _payload(response)  # type: ignore[return-value]


def get_audit(flow_id: str, *, trace_id: str,
              base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> list:
    response = _request("GET", f"/api/flows/{flow_id}/audit", trace_id=trace_id,
                        base_url=base_url, timeout=timeout)
    return _payload(response)  # type: ignore[return-value]


def _selftest(base_url: str, flow_id: str) -> int:
    trace_id = new_trace_id()
    request_id = new_request_id("req-flow-client")
    print("=" * 78)
    print("P0-02 flow_client self-test")
    print("=" * 78)
    print(f"base_url = {base_url}")
    print(f"trace_id = {trace_id}")
    print("  ↑ 用这串 uuid 去 Java 控制台日志里 grep：[trace=...] 应命中同一批请求")
    print()

    # 1) 读审计（阶段 A 唯一能返回真实数据的流程端点）
    rows = get_audit(flow_id, trace_id=trace_id, base_url=base_url)
    print(f"[1] GET /api/flows/{flow_id}/audit -> 200，{len(rows)} 行")
    for row in rows:
        print("    " + json.dumps(row, ensure_ascii=False))
    print("    断言通过：X-Trace-Id 原样回显")
    print()

    # 2) 不存在的单 → 404（结构化错误体，客户端不吞）
    try:
        get_audit("flow-does-not-exist", trace_id=trace_id, base_url=base_url)
        print("[2] 期望 404，但请求成功了 —— 失败")
        return 1
    except ContractError as exc:
        print(f"[2] GET audit(不存在的单) -> HTTP {exc.status} code={exc.code} message={exc.message}")
        assert exc.status == 404 and exc.code == "FLOW_NOT_FOUND"
        print("    断言通过：404 + FLOW_NOT_FOUND（错误体里的 trace_id 可用于排障）")
    print()

    # 3) 参数校验：头与 body.request_id 不一致 → 422
    try:
        # 注意：必须经过 _payload 才会把非 2xx 变成 ContractError —— _request 只负责发请求与断言 trace 回显
        _payload(_request("POST", "/api/flows", trace_id=trace_id,
                          body={"request_id": request_id, "item": "MacBook Pro", "qty": 1,
                                "reason": "for new hire", "cost_center": "DEV-01"},
                          extra_headers={REQUEST_ID_HEADER: "req-mismatch"},
                          base_url=base_url))
        print("[3] 期望 422，但请求成功了 —— 失败")
        return 1
    except ContractError as exc:
        print(f"[3] POST /api/flows(头体不一致) -> HTTP {exc.status} code={exc.code} message={exc.message}")
        assert exc.status == 422 and exc.code == "VALIDATION"
        print("    断言通过：422 + VALIDATION")
    print()

    # 4) 建单：阶段 A 由 FlowServiceStub 回答 501（业务实现是 [你敲]，阶段 B 才有真结果）
    try:
        created = create_flow(request_id=request_id, item="MacBook Pro", qty=1, reason="for new hire",
                              cost_center="DEV-01", trace_id=trace_id, base_url=base_url)
        print(f"[4] POST /api/flows(合法) -> 200 {json.dumps(created, ensure_ascii=False)}")
        assert created.get("state") == "PENDING_APPROVAL", "建单后的状态应为 PENDING_APPROVAL"
        print("    断言通过：建单成功且状态 = PENDING_APPROVAL")
    except ContractError as exc:
        print(f"[4] POST /api/flows(合法) -> HTTP {exc.status} code={exc.code}")
        print(f"    message = {exc.message}")
        if exc.status == 501 and exc.code == "NOT_IMPLEMENTED":
            print("    阶段 A 预期行为：FlowService 尚未实现（[你敲]），阶段 B 完成后这里应变成 200")
        else:
            print("    非预期错误 —— 失败")
            return 1
    print()

    print("=" * 78)
    print("SELFTEST PASS - trace 透传断言与契约错误体断言全部通过")
    print(f"Java 侧 grep 命令示例：Select-String -Path java-service/target/console-*.log -Pattern '{trace_id}'")
    print("=" * 78)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Java 流程端点客户端（P0-02 A3）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--flow-id", default=DEFAULT_FLOW_ID, help="用于读审计的已存在单据")
    args = parser.parse_args(argv)
    try:
        return _selftest(args.base_url, args.flow_id)
    except (ContractError, TracePropagationError) as exc:
        print(f"\nSELFTEST FAIL - {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
