"""mock-oa 的 Python 客户端（P0-01 冒烟核心）。

契约来源：docs/task-cards/P0-01-dsh-brief.md §3.1 / §3.3。

本模块负责三件在 P0-01 必须被"证掉"的事：

1. trace_id 由 **Python 生成**（标准 uuid4 小写带横线），经请求头 X-Trace-Id 交给 Java；
2. Java **必须原样回显**该 header —— 这里断言，不等就抛异常（静默通过等于没验）；
3. Java 侧的日志里能 grep 到同一个 uuid（响应头只能证明"回过话"，日志才能证明"落到了执行链路"）。

request_id 是幂等键，走 X-Request-Id 头；同 request_id 的第二次调用必须 dedup=true
且响应头 X-Dedup: true，且 approval_id / created_at 与首次完全一致。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx

TRACE_HEADER = "X-Trace-Id"
REQUEST_ID_HEADER = "X-Request-Id"
DEDUP_HEADER = "X-Dedup"
APPROVALS_PATH = "/api/mock/oa/approvals"
HEALTH_PATH = "/healthz"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class OaClientError(RuntimeError):
    """mock-oa 返回了非 200，或返回体不符合契约。"""


class TracePropagationError(AssertionError):
    """trace_id 没有被 Java 原样回显 —— 透传链路断了。"""


class DedupSemanticsError(AssertionError):
    """幂等语义不符合契约（首次该 dedup=false、重复该 dedup=true）。"""


def new_trace_id() -> str:
    """生成契约要求的 trace_id：标准 uuid4，小写带横线。"""
    return str(uuid.uuid4())


def create_approval(
    *,
    request_id: str,
    flow_id: str,
    title: str,
    amount: float,
    applicant: str,
    base_url: str = DEFAULT_BASE_URL,
    trace_id: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """创建一条 mock 审批单，返回服务端响应体（dict）。

    :param trace_id: 不传则内部生成；传了就用它（自测要拿它去 Java 日志里 grep，
        所以必须能把 id 从外面递进来）。
    :raises OaClientError: 非 200 响应（含错误体原文，便于排障）。
    :raises TracePropagationError: 响应头 X-Trace-Id 与传入的 trace_id 不一致。
    """
    response = _post_approval(
        request_id=request_id,
        flow_id=flow_id,
        title=title,
        amount=amount,
        applicant=applicant,
        base_url=base_url,
        trace_id=trace_id,
        timeout=timeout,
    )
    return response.json()


def _post_approval(
    *,
    request_id: str,
    flow_id: str,
    title: str,
    amount: float,
    applicant: str,
    base_url: str,
    trace_id: str | None,
    timeout: float,
) -> httpx.Response:
    """发请求 + 断言 trace 回显；返回原始 response（自测要看响应头 X-Dedup）。"""
    if not request_id:
        raise ValueError("request_id is required")

    effective_trace_id = trace_id or new_trace_id()
    payload = {
        "request_id": request_id,
        "flow_id": flow_id,
        "title": title,
        "amount": amount,
        "applicant": applicant,
    }
    headers = {
        TRACE_HEADER: effective_trace_id,
        REQUEST_ID_HEADER: request_id,
        "Content-Type": "application/json",
    }

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        response = client.post(APPROVALS_PATH, json=payload, headers=headers)

    if response.status_code != 200:
        raise OaClientError(
            f"POST {APPROVALS_PATH} -> HTTP {response.status_code}: {response.text}"
        )

    echoed = response.headers.get(TRACE_HEADER)
    if echoed != effective_trace_id:
        raise TracePropagationError(
            f"trace_id 未回显：sent={effective_trace_id!r} echoed={echoed!r}"
        )
    return response


def check_health(base_url: str = DEFAULT_BASE_URL, trace_id: str | None = None, timeout: float = 10.0) -> dict:
    """打 /healthz，同样断言 trace 回显。"""
    effective_trace_id = trace_id or new_trace_id()
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        response = client.get(HEALTH_PATH, headers={TRACE_HEADER: effective_trace_id})
    if response.status_code != 200:
        raise OaClientError(f"GET {HEALTH_PATH} -> HTTP {response.status_code}: {response.text}")
    echoed = response.headers.get(TRACE_HEADER)
    if echoed != effective_trace_id:
        raise TracePropagationError(
            f"/healthz trace_id 未回显：sent={effective_trace_id!r} echoed={echoed!r}"
        )
    return response.json()


def _selftest(base_url: str) -> int:
    """端到端自测：healthz → 首次创建 → 同 request_id 重复创建。"""
    suffix = uuid.uuid4().hex[:8]
    request_id = f"req-selftest-{suffix}"
    flow_id = f"flow-selftest-{suffix}"
    trace_id = new_trace_id()

    print("=" * 78)
    print("P0-01 oa_client self-test")
    print("=" * 78)
    print(f"base_url   = {base_url}")
    print(f"request_id = {request_id}   (幂等键，两次调用共用)")
    print(f"trace_id   = {trace_id}")
    print("  ↑ 这串 uuid 请到 Java 控制台日志里 grep：应命中同一请求的 [trace=...] 行")
    print()

    health = check_health(base_url=base_url, trace_id=trace_id)
    print(f"[1] GET /healthz  -> 200 body={json.dumps(health, ensure_ascii=False)}")
    print(f"    X-Trace-Id 回显 = {trace_id}  (断言通过)")
    print()

    first = _post_approval(
        request_id=request_id,
        flow_id=flow_id,
        title="采购 3 台 MacBook Pro",
        amount=60000,
        applicant="姜盛超",
        base_url=base_url,
        trace_id=trace_id,
        timeout=60.0,
    )
    first_body = first.json()
    print(f"[2] POST {APPROVALS_PATH} (首次) -> HTTP {first.status_code}")
    print(f"    body = {json.dumps(first_body, ensure_ascii=False)}")
    print(f"    headers: X-Trace-Id={first.headers.get(TRACE_HEADER)}  X-Dedup={first.headers.get(DEDUP_HEADER)}")
    if first_body.get("dedup") is not False:
        raise DedupSemanticsError(f"首次调用的 dedup 应为 false，实际 {first_body.get('dedup')!r}")
    if first.headers.get(DEDUP_HEADER) != "false":
        raise DedupSemanticsError(f"首次调用响应头 X-Dedup 应为 false，实际 {first.headers.get(DEDUP_HEADER)!r}")
    print("    断言通过：首次 dedup=false")
    print()

    second = _post_approval(
        request_id=request_id,
        flow_id=flow_id,
        title="采购 3 台 MacBook Pro",
        amount=60000,
        applicant="姜盛超",
        base_url=base_url,
        trace_id=trace_id,
        timeout=60.0,
    )
    second_body = second.json()
    print(f"[3] POST {APPROVALS_PATH} (同 request_id 第二次) -> HTTP {second.status_code}")
    print(f"    body = {json.dumps(second_body, ensure_ascii=False)}")
    print(f"    headers: X-Trace-Id={second.headers.get(TRACE_HEADER)}  X-Dedup={second.headers.get(DEDUP_HEADER)}")
    if second_body.get("dedup") is not True:
        raise DedupSemanticsError(f"重复调用的 dedup 应为 true，实际 {second_body.get('dedup')!r}")
    if second.headers.get(DEDUP_HEADER) != "true":
        raise DedupSemanticsError(f"重复调用响应头 X-Dedup 应为 true，实际 {second.headers.get(DEDUP_HEADER)!r}")
    if second_body.get("approval_id") != first_body.get("approval_id"):
        raise DedupSemanticsError("重复调用返回了不同的 approval_id —— 幂等没生效，建了两条单")
    if second_body.get("created_at") != first_body.get("created_at"):
        raise DedupSemanticsError("重复调用的 created_at 与首次不一致 —— 返回的不是首次结果")
    print("    断言通过：dedup=true 且 approval_id / created_at 与首次一致")
    print()
    print("=" * 78)
    print("SELFTEST PASS - trace 透传断言与幂等语义断言全部通过")
    print(f"Java 侧 grep 命令示例：Select-String -Path java-service/target/console.log -Pattern '{trace_id}'")
    print("=" * 78)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mock-oa Python 客户端（P0-01）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="mock-oa 地址")
    parser.add_argument("--selftest", action="store_true", help="跑端到端自测（默认行为）")
    args = parser.parse_args(argv)
    try:
        return _selftest(args.base_url)
    except (OaClientError, TracePropagationError, DedupSemanticsError) as exc:
        print(f"\nSELFTEST FAIL - {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
