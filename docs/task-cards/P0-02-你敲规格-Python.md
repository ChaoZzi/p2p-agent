# P0-02 「你敲」行为规格 — Python 侧（3 个文件）

> 角色：这 3 个文件是 `[你敲]` 档（agent loop 是你面试的深水区）—— **你亲手写**，Hermes 给行为规格。
> 依据：`docs/archive/P0-02-min-loop.md §4`（卡）、`docs/PRD.md §7`（harness/路由）、`docs/串讲.md`（主线）。
> 心法：**模型是"不确定的决策源"，代码是"确定的护栏"** —— 你写的每一个校验、每一次重试、每一轮上限，都是在给模型套笼子而不是替代它。

> **本规格按 v1.3 交付格式**（2026-10-16）：每节 = ① 这段代码是干嘛的 → ② 处在请求哪一步 → ③ 与上下文的联系 → ④ 字段/变量逐条说明 → ⑤ 完整参考实现（照抄可跑）+ 自测。

## 0. 通用约定

| 约定 | 值 |
|---|---|
| 包结构 | 保持扁平：`python-service/{harness,agents,tools,llm,trace,web}/` + `main.py`（与 P0-01 的 `tools/oa_client.py` 一致，不再套 `p2p/` 一层） |
| 依赖 | `uv add httpx pydantic`（pytest 已在 dev 依赖） |
| 时间/ID | `trace_id` 沿用 P0-01 的 `tools/oa_client.py::new_trace_id()` —— **一个 trace 走完整个请求** |
| **与 [dsh写] 的接缝（钉子）** | 你只依赖这两个接口，别自己造：<br>`llm/deepseek.py: complete_json(system: str, user: str, schema: dict, *, model="flash") -> dict`（JSON mode、超时+重试一次）<br>`trace/tracer.py: record(trace_id: str, step: str, payload: dict) -> None`（落 JSONL） |
| 状态存放 | P0-02 从简：**会话状态由调用方（`web/chat_api.py`，dsh 写）用内存 dict 按 session_id 保存**，你的文件只接收/返回 `state: dict`。P1 再换 Redis/SQLite（写在讲解里） |

## 1. `agents/intent.py`（先写它 —— 纯函数，最好测）

> **本节按 v1.3 交付格式**：① 干嘛的 → ② 在请求哪一步 → ③ 与上下文的联系 → ④ 字段/变量逐条说明 → ⑤ 完整参考实现（照抄可跑）。
>
> **动手前 · 文件级清单（v1.2）**
> 1. 建包目录：`python-service/agents/__init__.py`（空文件即可；没有它 → `ModuleNotFoundError: No module named 'agents'`）
> 2. 依赖 **已装好**（Hermes 代装，依赖属 `[dsh写]` 档）：`pydantic 2.14.1`。**本机 uv 不在系统 PATH**（它是 Hermes 自带那份），以后自己加依赖用：
>    `cd D:\Projects\interview-project\p2p-agent\python-service` → `& "C:\Users\姜盛超\AppData\Local\hermes\bin\uv.exe" add <包名>`
>    ⚠ 这个 `.venv` **没有 pip**（uv 建的默认不装）→ 不要用 `pip install` 兜底；PowerShell 里也不要写 `.\uv`（当前目录没有 uv.exe，会报"无法识别"）。
> 3. 新建 `python-service/agents/intent.py`（第 ⑤ 段是完整代码，照着敲）
> 4. 改完先跑一次 §5 的自测命令再提交

### ① 这段代码是干嘛的

把用户的一句人话（"我要买两台笔记本，给新来的同事用"）**翻译成字段** —— 物品 / 数量 / 事由 / 成本中心 —— 并**诚实说出"哪一项他没讲"**。它不查预算、不建单、不做任何业务判断：它只负责**听懂**，判断留给后面的人。

打比方：**翻译官 + 检票员**。翻译官把中文翻成"表格"，检票员检查表格填得对不对；**表格缺项就退回去让人补，绝不代填**。

### ② 它处在整个请求的哪一部分

一次请求（`POST /api/chat`）的**第 1 步**，夹在"用户说话"和"建单"之间：

```
用户一句话
 └─① agents/intent.py::extract()                    ← 你正在写的这个（本文件）
      └─② harness/agent.py::run()                    决策循环：拿着意图+缺失项，决定"反问"还是"调工具"
           └─③ harness/tool_registry.py::invoke("create_purchase_flow")
                └─④ tools/flow_client.py               HTTP 调用
                     └─⑤ Java FlowService.createFlow() 建单 + 3 条审计（你已敲完的那部分）
```

- **上游**：`harness/agent.py`（第 2 步）调它；再上游是 `web/chat_api.py`（dsh 写，收 HTTP 请求）。
- **下游**：它**不直接调任何工具**，只把结果**返回**给 `agent.py`；真正的建单发生在第 3 步。
- **没有它会怎样**：模型说什么就照做什么。用户只说"买笔记本"（没说数量和成本中心），系统会自己编一个数字去建单 —— **这是最危险的失败模式：数据凭空产生，而且看起来完全正常。**

### ③ 它与上下文的联系

| 关系 | 说明 |
|---|---|
| 入参 `text` | 用户原话；由 `agent.py` 从会话 `state` 里取出后传进来 |
| 入参 `trace_id` | 与 Java 侧审计、`trace/*.jsonl` 共用**同一个 uuid**（`tools/oa_client.py::new_trace_id()` 生成）→ 两端靠它对账 |
| 调 `llm/deepseek.py::complete_json(...)` | 它**唯一**允许碰"模型"的地方（**钉子接口**，别自己写 HTTP） |
| 调 `trace/tracer.py::record(...)` | 落 JSONL（**钉子接口**），一行一个步骤；实测 `from trace.tracer import record` 在 `python-service/` 下可正常导入 |
| 返回值 | 交给 `agent.py`：有缺失 → 生成反问（HITL 雏形）；`(None, [])` → 转人工 |
| 共享状态 | **无** —— 它是纯函数：只读入参、只写 trace，不碰会话 `state`、不碰数据库（所以它最好测；也是这三个文件里唯一能单独测的） |

### ④ 字段 / 变量逐条说明

**`PurchaseIntent`（模型必须产出的形状；也是喂给模型的 JSON Schema 的来源）**

| 字段 | 是什么 | 值从哪来 | 为什么需要 | 不填 / 填错会怎样 |
|---|---|---|---|---|
| `item` | 采购物品名 | 模型从用户话里抽 | 建单必填项 | 空串被 `min_length=1` 拦下 → 校验失败；只有空格时进"缺失"列表 |
| `qty` | 数量（整数） | 模型抽取 | 建单必填；P0-03 里是金额与审批链的依据 | 越界（`0` 或 `>100`）→ 校验失败 → 纠错重试 → 仍失败转人工；**不四舍五入、不截断** |
| `reason` | 采购事由 | 模型抽取 | 审计 detail 要写清"为什么买" | 空 → 校验失败或进缺失列表 |
| `cost_center` | 成本中心编码 | **用户话里有才有**，没有就是 `None` | 审计与预算归属 | **宁可为 `None` 也绝不许编**：编一个 `"DEFAULT"` 会让单据落到错误的成本中心，而且看起来完全正常 |

**`extract(text, trace_id)` 的入参与返回值**

| 名字 | 是什么 | 值从哪来 | 怎么被用 |
|---|---|---|---|
| `text` | 用户这一轮的原话 | `agent.py` 从会话 state 取 | 作为 `complete_json` 的 `user` 内容 |
| `trace_id` | 一次请求的 uuid | 上游生成（`new_trace_id()`） | 传给 `record()`；**不生成、不改写** |
| 返回 `PurchaseIntent` | 校验通过的意图 | 本函数产出 | `agent.py` 据此决定"反问"还是"建单" |
| 返回 `list[str]` | 缺失字段名（如 `["cost_center"]`） | `_missing_fields()` 算出 | `agent.py` 用它生成"请问成本中心是？" |
| 返回 `(None, [])` | **放弃**（两次产出都不合法） | 纠错重试仍失败 | 转人工（P0-03 接 HITL） |

**模块常量 / 局部变量**

| 名字 | 是什么 | 值 | 为什么 |
|---|---|---|---|
| `MAX_RETRY_ATTEMPTS` | 校验失败后**额外**重试次数 | `1` | 一次纠错的成本收益最高；再多说明**任务本身超出模型能力**（该换 pro 模型或转人工）= 路由策略的入口 |
| `MAX_QTY` | 数量上限 | `100` | 与 Java 侧 `qty` 校验边界保持同一份约定；**边界写进类型，不写进 `if`** |
| `STEP` | trace 的步骤名 | `"intent.extract"` | JSONL 能按步骤 grep；全项目步骤名不重复 |
| `SYSTEM_PROMPT` | 给模型的角色与规则 | 常量 | 稳定前缀利于服务端 prompt 缓存；**明确写"没说就返回 null，不许猜"** —— 防编造的第一道护栏 |
| `feedback` | 回喂给模型的校验错误原文 | Pydantic `ValidationError` 的文本 | 让**模型自己改**，而不是我们在代码里猜它的意图 |

### ⑤ 完整参考实现（照抄）

```python
"""意图抽取（P0-02 主体 §1）。

职责：用户一句话 → 结构化参数（PurchaseIntent）+ 缺失字段清单。
不做业务判断：不查预算、不建单、不碰会话 state。
三层护栏：JSON Schema 约束（给模型看）→ Pydantic 校验（我们说了算）→ 纠错重试一次（把错误原文回喂）。
"""

from __future__ import annotations

import pydantic

from llm.deepseek import complete_json            # 钉子接口：模型调用只走它
from trace.tracer import record                   # 钉子接口：trace 只走它

STEP = "intent.extract"          # trace 步骤名（全项目不重复，便于 grep 与回放）
MAX_RETRY_ATTEMPTS = 1           # 校验失败后"额外"重试几次：1 次（再多说明任务超出模型能力）
MAX_QTY = 100                    # 与 Java 侧 qty 边界同一份约定；写进类型，不写进 if


class PurchaseIntent(pydantic.BaseModel):
    """采购意图：模型必须产出的形状（它的 model_json_schema() 就是喂给模型的 schema）。"""

    item: str = pydantic.Field(min_length=1, max_length=100)   # 物品；空串直接被拦
    qty: int = pydantic.Field(ge=1, le=MAX_QTY)                # 数量；越界即校验失败
    reason: str = pydantic.Field(min_length=1)                 # 事由；审计要写
    cost_center: str | None = pydantic.Field(default=None)      # 关键：允许缺失，**不给默认值**


SYSTEM_PROMPT = (
    "你是企业采购流程的意图抽取器。从用户的中文描述里抽取采购申请的结构化字段。"
    "只抽用户明确说出的信息：用户没提成本中心，cost_center 就返回 null，绝对不要猜测或编造。"
    f"qty 必须是 1 到 {MAX_QTY} 之间的整数。"
)


def extract(text: str, trace_id: str) -> tuple[PurchaseIntent | None, list[str]]:
    """把一句话抽成 (意图, 缺失字段名列表)。

    校验失败 → 把错误原文回喂模型重试一次；再失败 → 返回 (None, []), 由上层转人工。
    """
    feedback = ""
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 2):          # 1 次原始 + 1 次纠错 = 2 轮
        raw = complete_json(
            SYSTEM_PROMPT,
            text + feedback,                                   # 第 2 轮带着错误原文
            PurchaseIntent.model_json_schema(),                # 由模型生成，不手写副本
        )
        try:
            intent = PurchaseIntent.model_validate(raw)         # 校验：我们说了算的那一层
        except pydantic.ValidationError as exc:
            feedback = f"\n\n上次输出未通过校验，错误如下：\n{exc}\n请修正后重新输出 JSON。"
            record(trace_id, STEP, {
                "attempt": attempt, "text": text, "raw": raw,
                "ok": False, "validation_error": str(exc),
            })
            continue                                           # 带着错误再问一次

        missing = _missing_fields(intent)                      # 校验过了，再看"缺什么"
        record(trace_id, STEP, {
            "attempt": attempt, "text": text, "raw": raw,
            "ok": True, "missing": missing,
        })
        return intent, missing

    record(trace_id, STEP, {"attempt": MAX_RETRY_ATTEMPTS + 1, "text": text, "gave_up": True})
    return None, []                                            # 交给上层转人工，绝不猜


def _missing_fields(intent: PurchaseIntent) -> list[str]:
    """哪些字段用户没讲清楚（业务上必须有）—— 只报缺失，**绝不代填默认值**。"""
    missing: list[str] = []
    if not intent.item.strip():
        missing.append("item")
    if not intent.reason.strip():
        missing.append("reason")
    if intent.cost_center is None or not intent.cost_center.strip():
        missing.append("cost_center")
    return missing
```

### 常见疑问：`model_validate` 这个校验方法在哪？我怎么没看到

**它是 Pydantic 自动生成的，不是我们写的。** 就像 Java 的 `record` 自动得到 `equals()`/`hashCode()`/构造器、Lombok 的 `@Data` 自动生成 getter 一样 —— `class PurchaseIntent(pydantic.BaseModel)` 继承 `BaseModel` 之后，
Pydantic 用元类在**类创建时**扫描那 4 行 `pydantic.Field(...)`，把一整套方法生成好贴在类上：

| 生成的方法 | 干什么 | 我们代码里在哪用 |
|---|---|---|
| `model_validate(dict)` | 从字典构造 + **逐字段套约束校验**；非法 → 抛 `ValidationError` | `intent = PurchaseIntent.model_validate(raw)` |
| `model_json_schema()` | 把字段约束导成 JSON Schema | `complete_json(..., PurchaseIntent.model_json_schema())` |
| `model_dump()` / `model_dump_json()` | 导出字典 / JSON（落审计、返前端时用） | P0-03 用 |
| `__init__` | 构造器，**同样会校验** | 测试里直接 `PurchaseIntent(item=..., qty=...)` |

**真正的校验器是 pydantic-core（Rust 实现）**，由 Field 的约束编译出来 —— 所以"**边界写进类型，不写进 `if`**"这句话是字面意思：我们一行 `if qty > 100` 都不写，非法值在进对象的那一刻就被拦在门外。

实测（`pydantic 2.14.1`）：传 `{"item":"", "qty":999, "reason":"", "cost_center":None}` →

```
3 validation errors for PurchaseIntent
item
  String should have at least 1 character [type=string_too_short, input_value='', input_type=str]
qty
  Input should be less than or equal to 100 [type=less_than_equal, input_value=999, input_type=int]
reason
  String should have at least 1 character [type=string_too_short, input_value='', input_type=str]
```

**这段英文文本就是代码里 `feedback` 变量回喂给模型的东西**（模型据此自己改）；程序化读数用 `exc.errors()`
（返回 `[((字段名,), 错误类型, 说明), ...]`，将来要给前端/审计就用它，别去 parse 那段文本）。

同一份约束导成的 JSON Schema 长这样（实测）：`required = ['item', 'qty', 'reason']` ——
**`cost_center` 不在 `required` 里**，`properties.cost_center = {'anyOf':[{'type':'string'},{'type':'null'}], 'default': None}`
→ 我们的设计意图（**允许缺失、不许编造**）被 schema 精确表达并传达给了模型。

加一根钉子：`pydantic` 的 v2 API **一律是 `model_` 前缀**（v1 是 `parse_obj` / `.dict()` / `.json()`）。换版本时这些名字会变 —— 面试可讲"为什么 v2 统一加前缀"（避免与业务字段名撞车 + 一眼分辨 v1/v2 代码）。

### ⚠ 三个高频写错点（实测过的报错，敲之前先看）

**① 缩进层级：常量和函数必须顶格（模块级），不能缩进到 `class` 里面。** 塞进类体会有两个后果：

- **Pydantic 在类定义那一刻就报错**（实测 pydantic 2.14）：
  ```
  PydanticUserError: A non-annotated attribute was detected: `SYSTEM_PROMPT = '你是企业采购流程的意图抽取器…'`.
  All model fields require a type annotation; if `SYSTEM_PROMPT` is not meant to be a field,
  you may be able to resolve this error by annotating it as a `ClassVar` ...
  ```
  —— 它把"没有类型注解的赋值"当成"你想声明字段"，于是要求你补类型。
- **即使绕过这一关，方法里裸写 `SYSTEM_PROMPT` 也拿不到**（实测 `NameError: name 'X' is not defined`）：
  **Python 的类体不是方法的外层作用域** —— 方法里要读类属性只能写 `self.X` 或 `类名.X`（实测两种都行）。
  ⚠ 这一点和 Java 相反（Java 的方法能直接看到外部类字段），所以从 Java 过来最容易踩。
  **正确姿势不是加 `self.`，而是把常量和函数都放到类外面。**

```
# ── 模块级：顶格，不缩进 ─────────────────────────────
STEP = "intent.extract"
MAX_RETRY_ATTEMPTS = 1
MAX_QTY = 100
SYSTEM_PROMPT = (...)          # ← 常量放这里，extract() 才看得见，别的模块也能 import

class PurchaseIntent(pydantic.BaseModel):   # ── 类体里只放"数据形状"
    item: str = ...
    qty: int = ...
    reason: str = ...
    cost_center: str | None = ...

def extract(text: str, trace_id: str):      # ── 动作也是模块级（不塞进类）
    ...

def _missing_fields(intent):                # 私有帮手同样模块级
    ...
```

**`extract` 为什么不该是类的方法**：① `PurchaseIntent` 是数据形状（纯声明 + 能导 schema），`extract` 是动作（调模型、写 trace），职责分开；② 它不需要实例状态，用了 `self` 反而要先造实例 —— 可它的产出**就是**那个实例；③ 规格与 `agent.py` 期望的签名是 `agents.intent.extract(text, trace_id)`；④ 模块级函数 `from agents.intent import extract` 一行就能测。**判断标准：这个函数需要实例状态吗？需要才放进类。**

**② `trace_id` 是 `str` 不是 `int`**：它是 uuid 字符串（`tools/oa_client.py::new_trace_id()` 生成），下游 `record()` 拿它当**文件名**（`trace/<trace_id>.jsonl`），还要和 Java 审计列对账。

**③ 重试轮数别算错**：`MAX_RETRY_ATTEMPTS = 1` 表示"额外重试 1 次"，总轮数 = **2**。
实测：`range(1, MAX_RETRY_ATTEMPTS + 1)` → `[1]`（**只跑 1 轮，纠错重试根本没发生**）；`range(1, MAX_RETRY_ATTEMPTS + 2)` → `[1, 2]` ✅。

**④ `complete_json` 是三个必填参数**（`system / user / schema`，`model` 是仅关键字且有默认值）：

```python
raw = complete_json(
    SYSTEM_PROMPT,
    text + feedback,                      # 第 2 轮会把校验错误原文带上
    PurchaseIntent.model_json_schema(),   # 让模型看到同一份约束
)
```

### 自测（写完先跑，别急着提交）

**PowerShell**（你在用的这个；`.venv` 里那台 python 直连，绕开 conda 的 `python` 和"uv 不在 PATH"两个坑）：
```powershell
cd D:\Projects\interview-project\p2p-agent\python-service
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe -c "import uuid; from agents.intent import extract; print(extract('买两台笔记本，给新来的同事用', str(uuid.uuid4())))"
```
**git-bash**（同一件事）：
```bash
cd D:/Projects/interview-project/p2p-agent/python-service
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "import uuid; from agents.intent import extract; print(extract('买两台笔记本，给新来的同事用', str(uuid.uuid4())))"
```
**期望现象**：打印出 `(PurchaseIntent(item='笔记本', qty=2, reason=..., cost_center=None), ['cost_center'])` ——
数量对、物品对、**cost_center 是 None 且进缺失列表**；然后 `trace/<那个uuid>.jsonl` 里多一行 `step=intent.extract`。
**如果它自己编了一个 cost_center（比如 "DEFAULT"）→ 护栏没生效，停下来找我。**

## 2. `harness/tool_registry.py`（工具的"身份证 + 检查站"）

> **本节按 v1.3 交付格式**：① 干嘛的 → ② 在请求哪一步 → ③ 与上下文的联系 → ④ 字段/变量逐条说明 → ⑤ 完整参考实现。
>
> **动手前 · 文件级清单**
> 1. 建包目录：`python-service/harness/__init__.py`（空文件）
> 2. 新建 `python-service/harness/tool_registry.py`（第 ⑤ 段是完整代码）
> 3. 不需要新依赖（只用标准库 `dataclasses` / `time` / `typing` + 已有的 `trace.tracer`）
> 4. ⚠ **命名坑（实测）**：项目里有 `python-service/trace/` 包，它和 **Python 标准库的 `trace` 同名**。
>    用 `python <别的目录的脚本>` 跑时，脚本所在目录会被插到 `sys.path[0]`（优先级高于 cwd）→
>    `from trace.tracer import record` 报 `ModuleNotFoundError: No module named 'trace.tracer'; 'trace' is not a package`。
>    **规避**：在 `python-service/` 目录下用 `python -c` / `python -m`（cwd 优先），或 `pytest`（`pyproject.toml` 已配 `pythonpath=["."]`）。

### ① 这段代码是干嘛的

工具的 **"身份证 + 检查站"**。它回答三个问题：**有哪些工具**（整理成给模型看的清单）、**这个工具收什么参数**（JSON Schema）、**这次调用该不该放行**（执行前校验）。

它**不实现任何业务** —— 不知道什么是采购单、不碰 HTTP、不碰数据库。它的 executor 是别人写的函数，它只负责决定"让不让它跑、按什么参数跑"。

打比方：**机场安检 + 登机口**。工具是旅客（有护照 = 参数 schema），registry 是安检口：护照不对（缺必填/类型错）当场拦下并说明原因；工具在里面自己摔了（抛异常），也由它记一笔、不让整个航班停飞。

### ② 它处在整个请求的哪一部分

```
用户一句话
 └─① agents/intent.py::extract()              ← 已完成 ✅
      └─② harness/agent.py::run()              决策循环（下一段）
           ├─ 模型说 {"action": "tool:create_purchase_flow", "args": {...}}
           └─③ harness/tool_registry.py::invoke()   ← 你正在写的这个
                ├─ 查表 → 校验参数 → 执行 executor
                └─④ tools/flow_client.py → HTTP → Java FlowService（已完成的 Java 侧）
                     └─ 结果作为"观察"回到 ②，模型再决策
```

- **上游**：`harness/agent.py`（第 2 步）在模型要求调用工具时调它。
- **下游**：它调 `Tool.executor`（实际是 `tools/flow_client.py` 里的函数），**不直接碰 HTTP**。
- **没有它会怎样**：模型给的参数会**原样**打到后端 —— 模型少传 `qty`、传个字符串 `"两台"`、甚至编一个不存在的工具名，全都变成 500/脏数据。更糟的是：工具一旦抛异常，整个 agent 循环直接崩，用户看到 500 而不是"我能自己修正"。

### ③ 它与上下文的联系

| 关系 | 说明 |
|---|---|
| 入参 `state` | 会话状态（`agent.py` 维护的 dict）。**必须含 `state["trace_id"]`** —— 本模块靠它写 trace，不额外加参数 |
| 入参 `args` | **模型产出的、完全不可信**的字典 → 所以必须先校验再执行 |
| 入参 `name` | 模型给的字符串（`"tool:create_purchase_flow"` 去掉前缀后传进来） |
| `Tool.parameters` | 来自工具自己的 JSON Schema，`schemas()` 把它交给模型 → **模型看到的边界 == 我们校验用的边界**（同一份，不手抄副本） |
| `Tool.executor` | `tools/flow_client.py` 里的函数，签名统一 `executor(state: dict, **args) -> dict` |
| 返回值 | **直接进"观察"上下文喂回模型** → 必须是结构化、能读懂的东西（这也是"错误也是观察"的前提） |
| 共享事务/数据库 | **无**。纯内存表 + 一次 trace 落盘 |

### ④ 字段 / 变量逐条说明

**`Tool`（一个工具的"身份证"）**

| 字段 | 是什么 | 值从哪来 | 为什么需要 | 不填 / 填错会怎样 |
|---|---|---|---|---|
| `name` | 工具唯一名，如 `create_purchase_flow` | 注册时手写 | 模型用它在 `action` 里点名；也是 trace 里的标识 | 空/重复 → `register()` 直接 `ValueError`（静默覆盖是事故源） |
| `description` | 给模型看的**"何时用"**（不是"是什么"） | 写工具的人 | 模型靠它决定"这轮该不该调它" | 写成"创建采购流程"（是什么）→ 模型不知道什么时候该用；要写"用户信息齐全、要真正建单时用" |
| `parameters` | 参数的 JSON Schema（`type: object`） | 工具自己声明 | ① 交给模型看 ② 用作校验依据（**同一份**） | 不是 object 类型 → `register()` 拒绝；缺 `required` → 缺参数也能过（脏数据进后端） |
| `executor` | 真正干活的函数 | `tools/flow_client.py` | 把"检查"和"执行"分开（registry 不认识实现） | 签名不是 `(state, **args)` → 调用时 `TypeError`；它抛异常会被 registry 接住变成 `EXECUTOR_FAILED` |
| `side_effect` | 是否有副作用（建单/审批 = True） | 写工具的人 | **本卡不参与逻辑**，P0-03 的权限分级（要先 HITL 确认）要用 | 标错 → P0-03 会把"只读查询"也拦下来要人工确认（体验崩），或把"建单"放过（危险） |

**`ToolRegistry` 的四个方法**

| 方法 | 干什么 | 失败时 |
|---|---|---|
| `register(tool)` | 注册；重名/空名/参数不是 object schema → **抛 `ValueError`** | 配置错误，启动期就该炸（不返回结构化错误） |
| `get(name)` | 取工具 | 没有 → 抛 `ToolNotFound`（同样属于"配置错误"） |
| `schemas()` | 给模型的工具清单 `[{"name","description","parameters"}]` | — |
| `invoke(name, args, state)` | **固定四步**：查表 → 校验 → 执行 → 记录；**永远返回 dict，不抛异常** | 全部转成结构化错误（见下表） |

**`invoke` 的入参 / 返回值**

| 名字 | 是什么 | 值从哪来 | 怎么被用 |
|---|---|---|---|
| `name` | 工具名 | `agent.py` 从模型的 `action` 里解析出来 | 查 `self._tools` |
| `args` | 参数字典 | **模型**（不可信） | 先 `_validate_args`，再 `executor(state=state, **args)` |
| `state` | 会话状态 | `agent.py` 维护 | 传给 executor；同时 `state["trace_id"]` 用于 trace（缺失时兜底 `"unknown"`，绝不因为 trace 崩业务） |
| 返回 `{"ok": True, "data": ...}` | 成功 | executor 的返回值 | 作为 observation 喂回模型 |
| 返回 `{"ok": False, "error": {"code","detail","tool"}}` | 失败 | 四步里任一失败 | **同样喂回模型** —— 模型能据此改参数重试 |

**四个错误码（跨语言契约，大写+下划线）**

| code | 什么时候 | detail 里写什么 |
|---|---|---|
| `TOOL_NOT_FOUND` | `name` 不在表里 | `unknown tool: xxx` + 可用工具列表（可选） |
| `INVALID_ARGS` | 缺必填 / 类型错 / enum 不符 / 多了不认识的键 | **列出缺哪些、允许哪些**（模型据此改） |
| `EXECUTOR_FAILED` | executor 抛了异常 | `异常类名: 消息`（绝不把堆栈塞给模型） |
| （成功） | — | `ok: True` + `data` |

**模块常量 / 私有类**

| 名字 | 值 | 为什么 |
|---|---|---|
| `STEP` | `"tool.invoke"` | trace 步骤名；与 `intent.extract` 同一套命名 |
| `_TYPE_CHECKS` | `{"string":..., "integer":..., ...}` | 最小可用类型校验表（**不引 jsonschema 依赖**，只覆盖我们会用到的 6 种） |
| `_InvalidArgs` | 内部异常 | 只在本模块里当"信号"用，对外**永远**变成 `INVALID_ARGS` |

**核心设计决策（面试必问）**：**工具出错返回结构化错误，而不是抛异常把 loop 炸掉。**
- loop 的天职是"根据观察再决策" —— **错误也是一种观察**：模型看到 `INVALID_ARGS: missing required args: ['qty']` 能自己补参数重试。
- 但护栏在 agent 侧：同一工具连续失败 2 次就不再给它机会（`agent.py` 的护栏之二）。
- 对照组：`register()` / `get()` 的错**照抛** —— 那是**配置错误**（写代码的人错了），必须在启动期暴露，不能伪装成"模型调用失败"。

### ⑤ 完整参考实现（照抄）

```python
"""工具注册表（P0-02 主体 §2）：工具的"身份证 + 检查站"。

职责：① 注册工具 ② 把工具描述整理成给模型看的清单 ③ 在**执行前**校验参数。
不关心工具内部怎么实现 —— executor 是别人写的函数，这里只判断"该不该让它跑"。
"""

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


@dataclass(frozen=True)
class Tool:
    name: str                              # 唯一名，如 "create_purchase_flow"
    description: str                       # 给模型看：**何时用**（不是"是什么"）
    parameters: dict[str, Any]             # JSON Schema（type=object）
    executor: Callable[..., Any]           # 真活：签名统一 executor(state, **args) -> dict
    side_effect: bool = False              # 建单/审批 = True；本卡不参与逻辑，P0-03 权限分级用


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name or not tool.name.strip():
            raise ValueError("tool name must not be blank")
        if tool.name in self._tools:
            raise ValueError(
                f"duplicate tool name: {tool.name} (silently overwriting is how incidents start)")
        if not isinstance(tool.parameters, dict) or tool.parameters.get("type") != "object":
            raise ValueError(f"tool {tool.name}: parameters must be an object JSON Schema")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(name) from exc

    def schemas(self) -> list[dict[str, Any]]:
        """给模型的工具清单：模型看到的就是我们校验时用的那份 schema。"""
        return [{"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in self._tools.values()]

    def invoke(self, name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """固定四步：查表 → 校验 → 执行 → 记录。**永远返回 dict，永远不抛**。"""
        started = time.perf_counter()
        args = dict(args or {})                       # 模型可能给 None 或非 dict

        tool = self._tools.get(name)                  # ① 查表
        if tool is None:
            return self._finish(name, args, state, started,
                                error=(TOOL_NOT_FOUND, f"unknown tool: {name}"))

        try:                                          # ② 校验参数
            _validate_args(tool, args)
        except _InvalidArgs as exc:
            return self._finish(name, args, state, started, tool=tool,
                                error=(INVALID_ARGS, str(exc)))

        try:                                          # ③ 执行
            data = tool.executor(state=state, **args)
        except Exception as exc:                      # noqa: BLE001 —— 刻意的：错误也是观察
            return self._finish(name, args, state, started, tool=tool,
                                error=(EXECUTOR_FAILED, f"{type(exc).__name__}: {exc}"))

        return self._finish(name, args, state, started, tool=tool, data=data)

    @staticmethod
    def _finish(name: str, args: dict[str, Any], state: dict[str, Any], started: float,
                *, tool: "Tool | None" = None, error: tuple[str, str] | None = None,
                data: Any = None) -> dict[str, Any]:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if error is None:
            result: dict[str, Any] = {"ok": True, "data": data}
        else:
            code, detail = error
            result = {"ok": False, "error": {"code": code, "detail": detail, "tool": name}}

        # ④ 记录：trace 失败绝不影响业务（tracer 内部已吞掉 OSError），trace_id 缺失也不崩
        record(str((state or {}).get("trace_id") or "unknown"), STEP, {
            "tool": name, "args": args, "ok": result["ok"],
            "error_code": None if error is None else error[0],
            "side_effect": None if tool is None else tool.side_effect,  # 查表失败时确实不知道
            "duration_ms": duration_ms,
        })
        return result


def _validate_args(tool: Tool, args: dict[str, Any]) -> None:
    """按 Tool.parameters 做最小可用校验：缺必填 / 类型错 / enum 不符 / 多余键。"""
    schema = tool.parameters
    properties: dict[str, Any] = schema.get("properties") or {}

    missing = [k for k in (schema.get("required") or []) if k not in args]
    if missing:
        raise _InvalidArgs(
            f"missing required args: {missing} (allowed keys: {sorted(properties)})")

    allow_extra = schema.get("additionalProperties") is True
    unknown = [k for k in args if k not in properties]
    if unknown and not allow_extra:
        raise _InvalidArgs(f"unknown args: {unknown} (allowed keys: {sorted(properties)})")

    for key, value in args.items():
        spec = properties.get(key) or {}
        expected = spec.get("type")
        if expected in _TYPE_CHECKS and not _TYPE_CHECKS[expected](value):
            raise _InvalidArgs(f"arg '{key}' must be {expected}, got {type(value).__name__}")
        if spec.get("enum") is not None and value not in spec["enum"]:
            raise _InvalidArgs(f"arg '{key}' must be one of {spec['enum']}, got {value!r}")
        if isinstance(value, str) and value.strip() == "" and int(spec.get("minLength", 0)) >= 1:
            raise _InvalidArgs(f"arg '{key}' must not be blank")
```

### 自测（四种情况都要看到结构化结果、loop 不崩）

```powershell
cd D:\Projects\interview-project\p2p-agent\python-service
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe -c "from harness.tool_registry import *; reg = ToolRegistry(); reg.register(Tool(name='echo', description='回显', parameters={'type':'object','properties':{'msg':{'type':'string'}},'required':['msg']}, executor=lambda state, msg: {'echo': msg})); print(reg.invoke('echo', {'msg':'hi'}, {'trace_id':'t1'})); print(reg.invoke('echo', {}, {'trace_id':'t1'})); print(reg.invoke('nope', {}, {'trace_id':'t1'}))"
```
**期望**：三条分别是 `{'ok': True, 'data': {'echo': 'hi'}}`、`{'ok': False, 'error': {'code': 'INVALID_ARGS', ...}}`、`{'ok': False, 'error': {'code': 'TOOL_NOT_FOUND', ...}}`，**都不抛异常**；
`trace/t1.jsonl` 里应有 3 行 `step=tool.invoke`。

## 3. `harness/agent.py`（最小 ReAct 决策循环 —— 本卡心脏）

**职责**：**"用户话 → 决策 → 执行 → 回填 → 再决策"** 的循环。它是编排者，不是业务实现者。

**规格**
```python
@dataclass
class AgentResult:
    reply: str                 # 给用户看的话（可能是反问）
    state: dict                # 更新后的会话状态（调用方负责持久化）
    tool_calls: list[dict]     # 本轮的调用记录（页面侧栏/审计展示用）
    status: str                # "ok" | "need_more_info" | "give_up"

def run(user_text: str, *, state: dict, trace_id: str, max_rounds: int = 3) -> AgentResult
```

**循环体（每轮固定动作）**
```
① 组装 messages：system（角色+规则，稳定） + 工具 schema（稳定） + 历史与观察（易变） + 本轮用户话
② 决策：complete_json(...) → 必须返回 JSON：
      {"thought": "...", "action": "tool:<name>" | "final", "args": {...} | "reply": "..."}
③ 解析：解析失败 → 纠错重试一次；再失败 → status="give_up"（不要静默吞掉）
④ action 是 tool: → registry.invoke(...) → 把结果作为 observation 追加进上下文 → 回到 ①
   action 是 final → 结束，返回 reply
⑤ 轮数用尽（max_rounds=3）→ status="need_more_info"，reply 说明"目前拿到什么、还差什么"
```

**四条护栏（每一条都要能在代码里指出来）**
1. **轮数上限**（3）：防死循环 + 防烧钱。
2. **同一工具连续失败 2 次 → 强制收口**：不要再让模型瞎试，直接给出"信息不足/需要人工"的结果。
3. **观察结果截断**（比如 2000 字符）：工具返回大了就截断并标注"已截断" —— 防 token 爆炸（这也是成本护栏的一部分）。
4. **反问 = final 的一种**：`{"action":"final","reply":"成本中心（cost_center）填什么？"}` → `status="need_more_info"`，并把**已抽到的参数留在 state 里**（下次用户补答时接着用，不从头问）。

**上下文分层（面试加分点）**
- 稳定前缀（system + 工具 schema）在前、易变内容（观察/历史）在后 → 命中服务端的 prompt 缓存，省钱又快。
- 别每轮把全部历史重新拼一遍长度爆炸的内容：只保留**最近 N 轮 + 关键状态摘要**。

**面试追问预演**
1. "为什么是'决策 → 执行 → 回填 → 再决策'，而不是一次生成全部步骤？" → **因为每步的结果会影响下一步**（工具会失败、会返回意料之外的值）。一次性生成=假设世界按剧本走；ReAct 把"世界的反馈"编进下一次决策。这是"Agent"和"脚本"的分界线。
2. "loop 里谁保证安全？" → 四条护栏 + 工具层校验 + 意图层校验；**模型负责可能性，代码负责边界**。
3. "怎么观测 loop 内部？" → 每轮落 trace JSONL（决策原文、参数、结果、耗时），配合 Java 侧同 trace_id 的审计 → 一条链路两端可对账。

---

## 4. 三者怎么串起来（对照 P0-02 演示脚本）

```
用户："我想申请采购 1 台 MacBook Pro，给新来的前端用"
  → chat_api 建 trace_id、取 state
  → agent.run()
      ├─ 决策：action=tool:extract_intent → intent.extract()
      │     返回 (intent, missing=["cost_center"])
      ├─ 决策：action=final（反问）→ reply="成本中心填什么？"  status=need_more_info
      └─ state 里留下已抽到的 item/qty/reason
用户："成本中心填 DEV-01"
  → agent.run(state 承接上轮)
      ├─ 决策：action=tool:create_purchase_flow → registry.invoke → tools/flow_client → Java POST /api/flows
      │     observation: {"ok":true,"data":{"flow_id":"...","state":"PENDING_APPROVAL"}}
      └─ 决策：action=final → reply="已建单，单号 xxx，当前待审批"
  → 页面侧栏显示状态 + 审计（Java 侧 audit_log，同 trace_id）
```

## 5. 建议写入顺序 + 自测

| 顺序 | 文件 | 写完立刻做的自测 |
|---|---|---|
| 1 | `agents/intent.py` | 三句真话：完整句 / 缺成本中心 / 数量非法（"买 -3 台"）→ 看返回与缺失列表 |
| 2 | `harness/tool_registry.py` | 假工具 echo 的四种调用（正常/缺参/未注册/执行体抛异常） |
| 3 | `harness/agent.py` | 用假工具跑通：一轮出结果 / 两轮（先工具后 final）/ 触发轮数上限 |

**写之前先把这 3 条问自己**（能答上来再动手，比写完再想省一半时间）：
1. 如果模型返回的 JSON 少了一个字段，我的代码在哪一行兜住它？
2. 如果工具执行抛异常，用户看到的是什么？loop 会不会死？
3. 如果用户补答"空"（什么都不填），会发生什么？（→ 应该再次反问，而不是编一个默认值）
