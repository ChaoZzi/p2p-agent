"""意图抽取（P0-02 主体 §1）。

职责：用户一句话 → 结构化参数（PurchaseIntent）+ 缺失字段清单。
不做业务判断：不查预算、不建单、不碰会话 state。
三层护栏：JSON Schema 约束（给模型看）→ Pydantic 校验（我们说了算）→ 纠错重试一次（把错误原文回喂）。
"""

from __future__ import annotations

import pydantic

from llm.deepseek import complete_json
from trace.tracer import record

STEP = "intent.extract"          # trace 步骤名（全项目不重复，便于 grep 与回放）
MAX_RETRY_ATTEMPTS = 1           # 校验失败后"额外"重试几次：1 次（再多说明任务超出模型能力）
MAX_QTY = 100
SYSTEM_PROMPT = (
    "你是企业采购流程的意图抽取器。从用户的中文描述里抽取采购申请的结构化字段。"
    "只抽用户明确说出的信息：用户没提成本中心，cost_center 就返回 null，绝对不要猜测或编造。"
    f"qty 必须是 1 到 {MAX_QTY} 之间的整数。"
)


# 与 Java 侧 qty 边界同一份约定；写进类型，不写进 if

class PurchaseIntent(pydantic.BaseModel):
    """采购意图：模型必须产出的形状（它的 model_json_schema() 就是喂给模型的 schema）。"""
    item: str = pydantic.Field(min_length=1, max_length=100)  # 物品；空串直接被拦
    qty: int = pydantic.Field(ge=1, le=MAX_QTY)  # 数量；越界即校验失败
    reason: str = pydantic.Field(min_length=1)  # 事由；审计要写
    cost_center: str | None = pydantic.Field(default=None)  # 关键：允许缺失，**不给默认值**

def extract(text:str,trace_id:str) -> tuple[PurchaseIntent | None,list[str]]:
    """把一句话抽成 (意图, 缺失字段名列表)。

        校验失败 → 把错误原文回喂模型重试一次；再失败 → 返回 (None, []), 由上层转人工。
    """
    feedback = ""
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 2):
        raw = complete_json(
            SYSTEM_PROMPT,
            text + feedback,  # 第 2 轮带着错误原文
            # model_json_schema() = 把类里的字段名 →
            # 一条条列出来，附上类型 + 约束，再汇总出哪些是必填（required）——输出一份 JSON 文本。
            PurchaseIntent.model_json_schema()  # 由模型生成，不手写副本
        )
        try:
            intent = PurchaseIntent.model_validate(raw)
        except pydantic.ValidationError as exc:
            feedback = f"\n\n上次输出未通过校验，错误如下：\n{exc}\n请修正后重新输出 JSON。"
            record(trace_id, STEP, {
                "attempt": attempt, "text": text, "raw": raw,
                "ok": False, "validation_error": str(exc),
            })
            continue
        missing_fields = _missing_fields(intent)
        record(trace_id, STEP, {
            "attempt": attempt, "text": text, "raw": raw,
            "ok": True, "missing": missing_fields,
        })
        return intent,missing_fields
    record(trace_id, STEP, {"attempt": MAX_RETRY_ATTEMPTS + 1,
            "text": text, "gave_up": True})
    return None, []

def _missing_fields(intent:PurchaseIntent) -> list[str]:
    """哪些字段用户没讲清楚（业务上必须有）—— 只报缺失，**绝不代填默认值**。"""
    missing: list[str] = []
    if not intent.item.strip():
        missing.append("item")
    if not intent.reason.strip():
        missing.append("reason")
    if intent.cost_center is None or not intent.cost_center.strip():
        missing.append("cost_center")
    return missing








