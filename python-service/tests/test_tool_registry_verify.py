"""【独立验证】harness/tool_registry.py —— 由 Hermes 编写，不是实现者自测。

真源：手抄自 docs/task-cards/P0-02-你敲规格-Python.md §2（职责 / 四个方法 / invoke 固定四步 /
四个错误码 / 字段表 / 核心设计决策）。**不读实现反推行为**。

覆盖：
  ① 结构层：Tool 是不可变 dataclass；常量名与值；`__init__.py` 为空
  ② register / get / schemas 的契约（重名不覆盖、配置错误照抛、schemas 与校验同源）
  ③ invoke 四步：成功 / TOOL_NOT_FOUND / INVALID_ARGS（缺必填·类型错·多余键·enum·空串·args非dict）/ EXECUTOR_FAILED
  ④ **永不抛**：任何坏输入都返回结构化 dict；只有 register/get 的配置错误才抛
  ⑤ 返回体形状：成功恰为 {ok,data}，失败恰为 {ok,error{code,detail,tool}}
  ⑥ trace：每次 invoke 一行，含 tool/args/ok/error_code/duration_ms/side_effect
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

import pytest

import harness.tool_registry as reg_mod
from harness.tool_registry import (
    EXECUTOR_FAILED,
    INVALID_ARGS,
    STEP,
    TOOL_NOT_FOUND,
    Tool,
    ToolNotFound,
    ToolRegistry,
)

# ── 真源（手抄规格 §2）───────────────────────────────
SPEC_STEP = "tool.invoke"
SPEC_CODES = {"TOOL_NOT_FOUND", "INVALID_ARGS", "EXECUTOR_FAILED"}
SPEC_TOOL_FIELDS = ["name", "description", "parameters", "executor", "side_effect"]
SPEC_SCHEMA_ENTRY_KEYS = {"name", "description", "parameters"}
SPEC_ERROR_KEYS = {"code", "detail", "tool"}          # 规格：{"ok": False, "error": {"code", "detail", "tool"}}
SPEC_OK_KEYS = {"ok", "data"}

SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string", "minLength": 1},
        "qty": {"type": "integer"},
        "cost_center": {"type": "string", "enum": ["CC-01", "CC-02"]},
    },
    "required": ["item", "qty"],
    "additionalProperties": False,
}


class Recorder:
    """executor 替身：记录收到了什么，返回可预期的 data。"""

    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[dict] = []
        self.raises = raises

    def __call__(self, state, **kwargs):
        self.calls.append({"state": state, "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        return {"echo": kwargs}


def make_registry(executor=None, *, side_effect=True, parameters=None) -> tuple[ToolRegistry, Recorder]:
    rec = Recorder() if executor is None else executor
    reg = ToolRegistry()
    reg.register(Tool(name="create_purchase_flow",
                      description="信息齐全、要真正建单时用",
                      parameters=SCHEMA if parameters is None else parameters,
                      executor=rec,
                      side_effect=side_effect))
    return reg, rec


@pytest.fixture()
def trace_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    return tmp_path


def _trace_lines(trace_dir: Path, trace_id: str) -> list[dict]:
    path = trace_dir / f"{trace_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _state(trace_id: str | None = None) -> dict:
    return {"trace_id": trace_id or str(uuid.uuid4()), "session_id": "s1"}


# ─────────────────────────────────── ① 结构层

def test_module_constants_match_spec():
    assert reg_mod.STEP == SPEC_STEP, f"STEP must be {SPEC_STEP!r}, got {reg_mod.STEP!r}"
    assert {reg_mod.TOOL_NOT_FOUND, reg_mod.INVALID_ARGS, reg_mod.EXECUTOR_FAILED} == SPEC_CODES


def test_tool_is_a_frozen_dataclass_with_spec_fields():
    assert dataclasses.is_dataclass(Tool), "Tool must be a dataclass"
    names = [f.name for f in dataclasses.fields(Tool)]
    assert names == SPEC_TOOL_FIELDS, f"Tool fields/order must be {SPEC_TOOL_FIELDS}, got {names}"
    defaults = {f.name: f.default for f in dataclasses.fields(Tool)}
    assert defaults["side_effect"] is False, "side_effect must default to False (only side-effecting tools opt in)"

    tool = Tool(name="x", description="d", parameters=SCHEMA, executor=lambda state: None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tool.name = "y"            # 不可变：改了必须抛


def test_package_marker_is_empty():
    init_file = Path(reg_mod.__file__).with_name("__init__.py")
    assert init_file.is_file() and init_file.read_bytes().strip() == b"", "__init__.py must stay empty"


def test_tool_not_found_is_a_lookup_error():
    assert issubclass(ToolNotFound, LookupError), (
        "ToolNotFound should subclass LookupError (it is a lookup/config error, not a business error)")


# ─────────────────────────────────── ② register / get / schemas

def test_register_then_get_returns_the_same_tool():
    reg, _ = make_registry()
    tool = reg.get("create_purchase_flow")
    assert tool.name == "create_purchase_flow" and tool.side_effect is True


def test_register_rejects_duplicate_name():
    reg, _ = make_registry()
    dup = Tool(name="create_purchase_flow", description="d2", parameters=SCHEMA, executor=lambda state: None)
    with pytest.raises(ValueError) as exc:
        reg.register(dup)
    assert "create_purchase_flow" in str(exc.value), "the error must name the duplicate tool"


@pytest.mark.parametrize("bad_name", ["", "   "])
def test_register_rejects_blank_name(bad_name):
    reg = ToolRegistry()
    with pytest.raises(ValueError):
        reg.register(Tool(name=bad_name, description="d", parameters=SCHEMA, executor=lambda state: None))


def test_register_rejects_non_object_schema():
    reg = ToolRegistry()
    with pytest.raises(ValueError):
        reg.register(Tool(name="x", description="d", parameters={"type": "array"}, executor=lambda state: None))


def test_get_unknown_tool_raises_tool_not_found():
    reg, _ = make_registry()
    with pytest.raises(ToolNotFound):
        reg.get("nope")


def test_schemas_exposes_name_description_parameters_only():
    reg, _ = make_registry()
    schemas = reg.schemas()
    assert len(schemas) == 1
    assert set(schemas[0]) == SPEC_SCHEMA_ENTRY_KEYS, f"schema entry keys must be {SPEC_SCHEMA_ENTRY_KEYS}"
    assert schemas[0]["parameters"] is SCHEMA, (
        "the schema handed to the model must be THE SAME object the validator uses (no hand-copied drift)")


def test_schemas_is_empty_for_fresh_registry():
    assert ToolRegistry().schemas() == []


# ─────────────────────────────────── ③/④ invoke：成功与永不抛

def test_invoke_success_shape_and_executor_wiring(monkeypatch, trace_dir):
    reg, rec = make_registry()
    state = _state()
    out = reg.invoke("create_purchase_flow", {"item": "laptop", "qty": 2}, state)

    assert set(out) == SPEC_OK_KEYS, f"success body must be exactly {SPEC_OK_KEYS}, got {set(out)}"
    assert out["ok"] is True
    assert out["data"] == {"echo": {"item": "laptop", "qty": 2}}, "executor must receive args as kwargs"
    assert rec.calls[0]["state"] is state, "executor must receive the caller's state object"
    assert rec.calls[0]["kwargs"] == {"item": "laptop", "qty": 2}


def test_invoke_unknown_tool_returns_structured_error(monkeypatch, trace_dir):
    reg, _ = make_registry()
    out = reg.invoke("nope", {}, _state())

    assert out["ok"] is False
    assert set(out["error"]) == SPEC_ERROR_KEYS, (
        f"error body must be exactly {SPEC_ERROR_KEYS} — the contract key is `code`, got {set(out['error'])}")
    assert out["error"]["code"] == TOOL_NOT_FOUND
    assert out["error"]["tool"] == "nope"
    assert "nope" in out["error"]["detail"]


def test_invoke_missing_required_returns_invalid_args(monkeypatch, trace_dir):
    reg, rec = make_registry()
    out = reg.invoke("create_purchase_flow", {"item": "laptop"}, _state())

    assert out["ok"] is False and out["error"]["code"] == INVALID_ARGS
    assert "qty" in out["error"]["detail"], "the detail must name the missing arg so the model can fix it"
    assert rec.calls == [], "validation must happen BEFORE the executor runs"


@pytest.mark.parametrize("args,needle", [
    ({"item": "laptop", "qty": "two"}, "must be integer"),          # 类型错
    ({"item": "", "qty": 2}, "must not be blank"),                   # minLength=1
    ({"item": "laptop", "qty": 2, "cost_center": "CC-99"}, "one of"),  # enum 外
    ({"item": "laptop", "qty": 2, "price": 9}, "unknown args"),       # 多余键（additionalProperties=False）
])
def test_invoke_validation_failures_are_structured(monkeypatch, trace_dir, args, needle):
    reg, rec = make_registry()
    out = reg.invoke("create_purchase_flow", args, _state())

    assert out["ok"] is False and out["error"]["code"] == INVALID_ARGS, f"args={args} must be INVALID_ARGS"
    assert needle in out["error"]["detail"], f"detail should mention {needle!r}, got {out['error']['detail']!r}"
    assert rec.calls == [], "a rejected call must never reach the executor"


@pytest.mark.parametrize("bad_args", [None, "item=laptop&qty=2", ["item"], 42])
def test_invoke_with_non_dict_args_never_raises(monkeypatch, trace_dir, bad_args):
    reg, rec = make_registry()
    try:
        out = reg.invoke("create_purchase_flow", bad_args, _state())
    except Exception as exc:                                            # noqa: BLE001
        pytest.fail(f"invoke must NEVER raise, but args={bad_args!r} raised {type(exc).__name__}: {exc}")
    assert out["ok"] is False and out["error"]["code"] == INVALID_ARGS
    assert "dict" in out["error"]["detail"] or "object" in out["error"]["detail"]
    assert rec.calls == []


def test_invoke_executor_exception_becomes_executor_failed(monkeypatch, trace_dir):
    rec = Recorder(raises=ZeroDivisionError("boom"))
    reg, _ = make_registry(executor=rec)
    out = reg.invoke("create_purchase_flow", {"item": "laptop", "qty": 2}, _state())

    assert out["ok"] is False and out["error"]["code"] == EXECUTOR_FAILED
    assert "ZeroDivisionError" in out["error"]["detail"], "the detail must carry the exception class name"
    assert "Traceback" not in out["error"]["detail"], "never dump a traceback into the model's observation"


def test_invoke_never_raises_for_a_battery_of_bad_inputs(monkeypatch, trace_dir):
    reg, _ = make_registry()
    bad_cases = [
        ("nope", {}), ("create_purchase_flow", {}), ("create_purchase_flow", {"qty": 2}),
        ("create_purchase_flow", {"item": "x", "qty": True}),      # bool 不是 integer
        ("create_purchase_flow", {"item": "x", "qty": 2, "zzz": 1}),
        ("", {}), (None, {}),
    ]
    for name, args in bad_cases:
        out = reg.invoke(name, args, _state())                    # 不 try/except：只要抛，测试就红
        assert isinstance(out, dict) and out["ok"] is False, f"case {name!r}/{args!r} must return a dict"


# ─────────────────────────────────── ⑥ trace

def test_trace_written_on_success(monkeypatch, trace_dir):
    reg, _ = make_registry(side_effect=True)
    tid = str(uuid.uuid4())
    reg.invoke("create_purchase_flow", {"item": "laptop", "qty": 2}, _state(tid))

    lines = _trace_lines(trace_dir, tid)
    assert len(lines) == 1, f"one invoke -> one trace line, got {len(lines)}"
    assert lines[0]["step"] == SPEC_STEP and lines[0]["trace_id"] == tid
    p = lines[0]["payload"]
    assert p["tool"] == "create_purchase_flow" and p["ok"] is True
    assert p["args"] == {"item": "laptop", "qty": 2}
    assert p["error_code"] is None
    assert p["side_effect"] is True, "trace must expose side_effect (P0-03 permission grading reads it)"
    assert isinstance(p["duration_ms"], (int, float))


def test_trace_written_on_failure_including_unknown_tool(monkeypatch, trace_dir):
    reg, _ = make_registry()
    tid = str(uuid.uuid4())
    reg.invoke("create_purchase_flow", {}, _state(tid))     # INVALID_ARGS
    reg.invoke("ghost", {}, _state(tid))                    # TOOL_NOT_FOUND

    lines = _trace_lines(trace_dir, tid)
    assert len(lines) == 2, f"two invokes -> two trace lines, got {len(lines)}"
    assert lines[0]["payload"]["error_code"] == INVALID_ARGS
    assert lines[1]["payload"]["error_code"] == TOOL_NOT_FOUND
    assert lines[1]["payload"]["side_effect"] is None, "unknown tool -> we cannot know side_effect"


def test_trace_falls_back_to_unknown_trace_id(monkeypatch, trace_dir):
    reg, _ = make_registry()
    out = reg.invoke("create_purchase_flow", {"item": "laptop", "qty": 2}, {})   # state 里没有 trace_id
    assert out["ok"] is True, "a missing trace_id must NOT break the business call"
    assert _trace_lines(trace_dir, "unknown"), "trace must still be written under 'unknown'"
