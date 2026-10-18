"""【独立验证】agents/intent.py —— 由 Hermes 编写，不是实现者自测。

真源：手抄自 docs/task-cards/P0-02-你敲规格-Python.md §1（职责 / 规格签名 / 行为规格 1~5 / 字段表）。
**不读实现反推行为**：所有期望值写在本文件顶部的 SPEC_* 常量里，改坏了测试立刻指出是哪一条。

覆盖：
  ① 结构层：extract / _missing_fields 必须是**模块级函数**（agent.py 按 agents.intent.extract 调），
     `__init__.py` 必须是空文件（包标记）
  ② 护栏：模型输出合法 → 直接返回；非法 → 把**校验错误原文回喂**重试一次；两次都非法 → (None, [])
  ③ 诚实：用户没说的字段进 missing 列表，**绝不编造默认值**
  ④ 契约：喂给模型的就是 PurchaseIntent.model_json_schema() 本身（required 不含 cost_center）
  ⑤ 可观测：每一次尝试都落一行 trace（含 attempt / ok / raw；失败行带 validation_error）
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path

import pytest

import agents.intent as intent_mod

# ── 真源（手抄规格 §1，不来自实现）──────────────────────────────
SPEC_STEP = "intent.extract"                      # 行为规格 5：全过程落 trace 的步骤名
SPEC_MAX_QTY = 100                                # 行为规格 4：qty 上界
SPEC_RETRY_ATTEMPTS = 1                           # 行为规格 2：校验失败后"重试一次"
SPEC_MISSING_WHEN_ABSENT = ["cost_center"]        # 行为规格 3：cost_center 缺失要报，不许代填
SPEC_REQUIRED_FIELDS = {"item", "qty", "reason"}   # 字段表：必填三项
SPEC_OPTIONAL_FIELDS = {"cost_center"}            # 字段表：允许缺失的一项


def _valid(**overrides):
    """一条合法的模型输出（模拟 complete_json 的返回）。"""
    payload = {"item": "laptop", "qty": 2, "reason": "for new hire", "cost_center": "CC-01"}
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not _OMIT}


class _Omit:
    pass


_OMIT = _Omit()


class FakeLLM:
    """替身：按脚本逐次返回，并记录每次收到的 (system, user, schema)。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, system, user, schema, **kwargs):
        self.calls.append({"system": system, "user": user, "schema": schema, "kwargs": kwargs})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.fixture()
def trace_dir(tmp_path, monkeypatch):
    """trace 落到临时目录，别污染仓库的 trace/。"""
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    return tmp_path


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr(intent_mod, "complete_json", fake)
    return fake


def _trace_lines(trace_dir: Path, trace_id: str) -> list[dict]:
    path = trace_dir / f"{trace_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ─────────────────────────────────────── ① 结构层

def test_extract_is_a_module_level_function():
    extract = getattr(intent_mod, "extract", None)
    assert extract is not None, (
        "extract must live at MODULE level (agent.py calls agents.intent.extract(text, trace_id)); "
        "if it is defined inside the model class, `from agents.intent import extract` fails"
    )
    assert inspect.isfunction(extract), f"extract must be a plain function, got {type(extract).__name__}"
    params = list(inspect.signature(extract).parameters)
    assert params == ["text", "trace_id"], (
        f"extract signature must be (text, trace_id) -- delete the leftover `self,` -- got {params}"
    )
    sig = inspect.signature(extract)

    def _ann(name: str) -> str:
        ann = sig.parameters[name].annotation
        return ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))

    assert _ann("text") == "str", f"text must be annotated `str`, got {_ann('text')!r}"
    assert _ann("trace_id") == "str", (
        f"trace_id must be annotated `str` (it is a uuid and becomes trace/<id>.jsonl), got {_ann('trace_id')!r}"
    )


def test_missing_fields_is_a_module_level_helper():
    helper = getattr(intent_mod, "_missing_fields", None)
    assert helper is not None, "_missing_fields must also be module level (it needs no instance state)"
    assert inspect.isfunction(helper)


def test_package_marker_is_empty():
    init_file = Path(intent_mod.__file__).with_name("__init__.py")
    assert init_file.is_file(), "__init__.py must exist (package marker)"
    assert init_file.read_bytes().strip() == b"", (
        "__init__.py must stay EMPTY: it runs on every `import agents.*`; business code there is a side effect"
    )


def test_constants_match_spec():
    assert intent_mod.STEP == SPEC_STEP
    assert intent_mod.MAX_QTY == SPEC_MAX_QTY
    assert intent_mod.MAX_RETRY_ATTEMPTS == SPEC_RETRY_ATTEMPTS
    assert intent_mod.MAX_QTY in _schema()["properties"]["qty"].values(), (
        "MAX_QTY must appear as qty's maximum in the schema handed to the model"
    )


def _schema() -> dict:
    return intent_mod.PurchaseIntent.model_json_schema()


# ─────────────────────────────────────── ④ 契约：喂给模型的 schema

def test_schema_handed_to_the_model_is_the_model_schema(monkeypatch, trace_dir):
    fake = _patch_llm(monkeypatch, FakeLLM(_valid()))
    intent_mod.extract("buy a laptop", str(uuid.uuid4()))

    assert fake.calls[0]["schema"] == intent_mod.PurchaseIntent.model_json_schema(), (
        "the schema passed to complete_json must BE PurchaseIntent.model_json_schema() (one source of truth)"
    )


def test_schema_marks_cost_center_optional_and_qty_bounded():
    schema = _schema()
    required = set(schema.get("required", []))
    assert required == SPEC_REQUIRED_FIELDS, f"required must be {SPEC_REQUIRED_FIELDS}, got {required}"
    assert set(schema["properties"]) >= SPEC_REQUIRED_FIELDS | SPEC_OPTIONAL_FIELDS
    assert "cost_center" not in required, "cost_center must stay optional (missing -> ask the user)"
    qty = schema["properties"]["qty"]
    assert qty["minimum"] == 1 and qty["maximum"] == SPEC_MAX_QTY, f"qty bounds leaked: {qty}"


# ─────────────────────────────────────── ² 护栏

def test_happy_path_returns_intent_and_no_missing(monkeypatch, trace_dir):
    fake = _patch_llm(monkeypatch, FakeLLM(_valid()))

    intent, missing = intent_mod.extract("buy a laptop for the new hire", str(uuid.uuid4()))

    assert intent is not None, "a valid model output must produce an intent"
    assert (intent.item, intent.qty, intent.reason, intent.cost_center) == ("laptop", 2, "for new hire", "CC-01")
    assert missing == [], f"nothing is missing when all fields are present, got {missing}"
    assert len(fake.calls) == 1, "a valid first answer must not trigger a retry"


def test_invalid_output_is_retried_once_with_the_error_fed_back(monkeypatch, trace_dir):
    bad = _valid(qty=999)          # 越界：Pydantic 必拒
    fake = _patch_llm(monkeypatch, FakeLLM(bad, _valid(qty=3)))

    intent, missing = intent_mod.extract("buy three laptops", str(uuid.uuid4()))

    assert intent is not None and intent.qty == 3, "the corrected second answer must be accepted"
    assert len(fake.calls) == SPEC_RETRY_ATTEMPTS + 1, "exactly one corrective retry"
    second_user = fake.calls[1]["user"]
    assert "buy three laptops" in second_user, "the retry must still carry the original user text"
    assert "less_than_equal" in second_user or "100" in second_user, (
        "the retry must feed the validation error back to the model, got: " + second_user[:300]
    )


def test_gives_up_after_retry_and_returns_none(monkeypatch, trace_dir):
    fake = _patch_llm(monkeypatch, FakeLLM(_valid(qty=0), _valid(qty=999)))

    intent, missing = intent_mod.extract("buy nothing", str(uuid.uuid4()))

    assert intent is None, "two invalid answers must yield None (hand over to a human), not a guessed intent"
    assert missing == [], "the (None, []) contract: no field list on give-up"
    assert len(fake.calls) == SPEC_RETRY_ATTEMPTS + 1, "give-up must happen after exactly 1 retry"


@pytest.mark.parametrize("qty,ok", [(1, True), (100, True), (0, False), (101, False)])
def test_qty_boundaries(monkeypatch, trace_dir, qty, ok):
    fake = _patch_llm(monkeypatch, FakeLLM(_valid(qty=qty)))
    intent, _ = intent_mod.extract("buy some", str(uuid.uuid4()))
    if ok:
        assert intent is not None and intent.qty == qty, f"qty={qty} must be accepted"
    else:
        assert intent is None, f"qty={qty} is out of range and must be rejected"
        assert len(fake.calls) == SPEC_RETRY_ATTEMPTS + 1, "out-of-range qty cannot be fixed by retrying the same value"


# ─────────────────────────────────────── ³ 诚实：不编造

def test_absent_cost_center_is_reported_missing_and_never_invented(monkeypatch, trace_dir):
    without_cc = {"item": "laptop", "qty": 2, "reason": "for new hire"}   # 模型没说成本中心
    _patch_llm(monkeypatch, FakeLLM(without_cc))

    intent, missing = intent_mod.extract("buy a laptop, I didn't say which cost center", str(uuid.uuid4()))

    assert intent is not None
    assert intent.cost_center is None, f"must stay None, got {intent.cost_center!r}"
    assert missing == SPEC_MISSING_WHEN_ABSENT, f"missing must be {SPEC_MISSING_WHEN_ABSENT}, got {missing}"
    assert "DEFAULT" not in json.dumps(intent.model_dump(), ensure_ascii=False).upper(), (
        "the code must NEVER substitute a default cost center"
    )


def test_blank_cost_center_counts_as_missing(monkeypatch, trace_dir):
    _patch_llm(monkeypatch, FakeLLM(_valid(cost_center="   ")))
    _, missing = intent_mod.extract("buy a laptop", str(uuid.uuid4()))
    assert missing == SPEC_MISSING_WHEN_ABSENT, f"whitespace-only cost_center is 'not told', got {missing}"


def test_present_fields_are_never_reported_missing(monkeypatch, trace_dir):
    _patch_llm(monkeypatch, FakeLLM(_valid()))
    _, missing = intent_mod.extract("buy a laptop", str(uuid.uuid4()))
    for field in SPEC_REQUIRED_FIELDS:
        assert field not in missing, f"{field} is present and must not be reported missing"


def test_whitespace_only_item_is_reported_missing(monkeypatch, trace_dir):
    # min_length=1 只拦空串；" " 能过 Pydantic，必须由 _missing_fields 的 strip() 兜住
    _patch_llm(monkeypatch, FakeLLM(_valid(item=" ")))
    intent, missing = intent_mod.extract("buy a laptop", str(uuid.uuid4()))
    assert intent is not None, "' ' passes the type-level check by design"
    assert "item" in missing, "whitespace-only item must be reported missing"


# ─────────────────────────────────────── ⁵ 可观测：trace

def test_trace_records_the_successful_attempt(monkeypatch, trace_dir):
    trace_id = str(uuid.uuid4())
    _patch_llm(monkeypatch, FakeLLM(_valid()))

    intent_mod.extract("buy a laptop", trace_id)

    lines = _trace_lines(trace_dir, trace_id)
    assert len(lines) == 1, f"one attempt -> one trace line, got {len(lines)}"
    assert lines[0]["step"] == SPEC_STEP, f"step must be {SPEC_STEP}"
    assert lines[0]["trace_id"] == trace_id
    payload = lines[0]["payload"]
    assert payload["ok"] is True and payload["attempt"] == 1
    assert payload["raw"] == _valid() and payload["text"] == "buy a laptop"
    assert payload["missing"] == []


def test_trace_records_the_failed_attempt_and_the_give_up(monkeypatch, trace_dir):
    trace_id = str(uuid.uuid4())
    _patch_llm(monkeypatch, FakeLLM(_valid(qty=999), _valid(qty=999)))

    intent_mod.extract("buy too many", trace_id)

    lines = _trace_lines(trace_dir, trace_id)
    assert len(lines) == 3, f"2 attempts + 1 give-up marker = 3 trace lines, got {len(lines)}"
    assert lines[0]["payload"]["ok"] is False
    assert lines[0]["payload"]["validation_error"], "the failed line must carry the validation error"
    assert lines[1]["payload"]["ok"] is False
    assert lines[2]["payload"].get("gave_up") is True, "the give-up must be observable in the trace"
