from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from trace.tracer import record            # 钉子接口：trace 只走它

STEP = "tool.invoke"                       # trace 步骤名（与 intent.extract 同一套命名）

TOOL_NOT_FOUND = "TOOL_NOT_FOUND"          # 没有这个工具
INVALID_ARGS = "INVALID_ARGS"              # 缺必填 / 类型错 / 多了不认识的键
EXECUTOR_FAILED = "EXECUTOR_FAILED"        # 工具自己跑炸了（异常由这里接住）

# 最小可用类型校验（不引 jsonschema 依赖，只覆盖我们工具会用到的类型）
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}

class ToolNotFound(LookupError):
    """get() 查未注册的工具 → 抛这个（配置错误，不是业务错误）。"""


class _InvalidArgs(Exception):
    """内部信号：参数不合法。只在本模块内部用，对外统一变成 INVALID_ARGS。"""

# 定义一个不可变的数据类 其实例不可变——创建后不能改字段，赋值会抛异常
@dataclass(frozen=True)
class Tool:
    name: str                              # 唯一名，如 "create_purchase_flow"
    description: str                       # 给模型看：**何时用**（不是"是什么"）
    parameters: dict[str, Any]             # JSON Schema（type=object）
    executor: Callable[..., Any]           # 真活：签名统一 executor(state, **args) -> dict
    side_effect: bool = False              # 建单/审批 = True；本卡不参与逻辑，P0-03 权限分级用

class ToolRegistry:
    def __init__(self) -> None:
        self.tools : dict[str,Tool] = {}
    def register(self,tool:Tool) -> None:
        if not tool.name or not tool.name.strip():
            raise ValueError("tool name must not be blank")
        if tool.name in self.tools:
            raise ValueError(
                f"duplicate tool name: {tool.name} (silently overwriting is how incidents start)")
        if not isinstance(tool.parameters,dict) or not tool.parameters.get("type") == "object":
            raise ValueError(f"tool {tool.name}: parameters must be an object JSON Schema")
        self.tools[tool.name] = tool

    def get(self,name:str) -> Tool:
        try:
            return self.tools[name]
        except KeyError as exc:
            raise ToolNotFound(name) from exc

    def schemas(self) -> list[dict[str,Any]]:
        return [{"name":t.name,"description":t.description,"parameters":t.parameters}
                for t in self.tools.values()]

    @staticmethod
    def _finish(name: str, args: dict[str, Any], state: dict[str, Any], started: float,
                *, tool: "Tool | None" = None, error: tuple[str, str] | None = None,
                data: Any = None) -> dict[str, Any]:
        duration_ms = round((time.perf_counter() - started) * 1000)
        if error is None:
            result : dict[str,Any] = {"ok":True,"data":data}
        else:
            error_code,detail = error
            result = {"ok":False,"error":{"code":error_code,"detail":detail,"tool":name}}
        record(str((state or {}).get("trace_id") or "unknown"),STEP,{
            "tool": name, "args": args, "ok": result["ok"],
            "error_code": None if error is None else error[0],
            "side_effect": None if tool is None else tool.side_effect,  # 查表失败时确实不知道
            "duration_ms": duration_ms,
        })
        return result

    def invoke(self, name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """固定四步：查表 → 校验 → 执行 → 记录。**永远返回 dict，永远不抛**。"""
        started = time.perf_counter()
        raw_args = args  # 模型原样给的（trace 里要留证据）
        args = args if isinstance(args, dict) else {}  # 非 dict 一律当空，绝不 dict(str) 炸掉

        tool = self.tools.get(name)  # ① 查表
        if tool is None:
            return self._finish(name, raw_args, state, started,
                                error=(TOOL_NOT_FOUND, f"unknown tool: {name}"))

        if not isinstance(raw_args, dict):  # 模型给了字符串/列表/None → 结构化拒绝
            return self._finish(name, raw_args, state, started, tool=tool,
                                error=(INVALID_ARGS,
                                       f"args must be an object (dict), got {type(raw_args).__name__}"))

        try:  # ② 校验参数
            _validate_args(tool, args)
        except _InvalidArgs as exc:
            return self._finish(name, args, state, started, tool=tool,
                                error=(INVALID_ARGS, str(exc)))

        try:  # ③ 执行
            data = tool.executor(state=state, **args)
        except Exception as exc:  # noqa: BLE001 —— 刻意的：错误也是观察
            return self._finish(name, args, state, started, tool=tool,
                                error=(EXECUTOR_FAILED, f"{type(exc).__name__}: {exc}"))

        return self._finish(name, args, state, started, tool=tool, data=data)

def _validate_args(tool: Tool, args: dict[str, Any]) -> None:
    """按 Tool.parameters 做最小可用校验：缺必填 / 类型错 / enum 不符 / 多余键。"""
    schema = tool.parameters
    properties: dict[str, Any] = schema.get("properties") or {}
    missing = [k for k in (schema.get("required")or []) if k not in args]
    if missing:
        raise _InvalidArgs(
            f"missing required args: {missing} (allowed keys: {sorted(properties)})")
    allow_extra = schema.get("additionalProperties") is True
    unknown = [k for k in args if k not in properties]
    if unknown and not allow_extra:
        raise _InvalidArgs(f"unknown args: {unknown} (allowed keys: {sorted(properties)})")
    for key,value in args.items():
        spec = properties.get(key) or {}
        expected = spec.get("type")
        if expected in _TYPE_CHECKS and not _TYPE_CHECKS[expected](value):
            raise _InvalidArgs(f"arg '{key}' must be {expected}, got {type(value).__name__}")
        if spec.get("enum") is not None and value not in spec["enum"]:
            raise _InvalidArgs(f"arg '{key}' must be one of {spec['enum']}, got {value!r}")
        if isinstance(value, str) and value.strip() == "" and int(spec.get("minLength", 0)) >= 1:
            raise _InvalidArgs(f"arg '{key}' must not be blank")