# P0-02 「你敲」行为规格 — Python 侧（3 个文件）

> 角色：这 3 个文件是 `[你敲]` 档（agent loop 是你面试的深水区）—— **你亲手写**，Hermes 给行为规格。
> 依据：`docs/task-cards/P0-02-min-loop.md §4`（卡）、`docs/PRD.md §7`（harness/路由）、`docs/串讲.md`（主线）。
> 心法：**模型是"不确定的决策源"，代码是"确定的护栏"** —— 你写的每一个校验、每一次重试、每一轮上限，都是在给模型套笼子而不是替代它。

## 0. 通用约定

| 约定 | 值 |
|---|---|
| 包结构 | 保持扁平：`python-service/{harness,agents,tools,llm,trace,web}/` + `main.py`（与 P0-01 的 `tools/oa_client.py` 一致，不再套 `p2p/` 一层） |
| 依赖 | `uv add httpx pydantic`（pytest 已在 dev 依赖） |
| 时间/ID | `trace_id` 沿用 P0-01 的 `tools/oa_client.py::new_trace_id()` —— **一个 trace 走完整个请求** |
| **与 [dsh写] 的接缝（钉子）** | 你只依赖这两个接口，别自己造：<br>`llm/deepseek.py: complete_json(system: str, user: str, schema: dict, *, model="flash") -> dict`（JSON mode、超时+重试一次）<br>`trace/tracer.py: record(trace_id: str, step: str, payload: dict) -> None`（落 JSONL） |
| 状态存放 | P0-02 从简：**会话状态由调用方（`web/chat_api.py`，dsh 写）用内存 dict 按 session_id 保存**，你的文件只接收/返回 `state: dict`。P1 再换 Redis/SQLite（写在讲解里） |

## 1. `agents/intent.py`（先写它 —— 纯函数，最好测）

**职责**：把用户一句话变成**结构化参数**，并说清"还缺什么"。它**不**做业务判断（不查预算、不建单）。

**规格**
```python
class PurchaseIntent(BaseModel):
    item: str = Field(min_length=1, max_length=100)
    qty: int = Field(ge=1, le=100)
    reason: str = Field(min_length=1)
    cost_center: str | None = None          # 关键：允许缺失，不设默认值

def extract(text: str, trace_id: str) -> tuple[PurchaseIntent | None, list[str]]:
    """返回 (意图, 缺失字段名列表)。校验失败且纠错重试仍失败 → (None, []) 由上层转人工。"""
```

**行为规格**
1. 给模型一份 **JSON Schema**（由 `PurchaseIntent.model_json_schema()` 生成，别手写一份会漂的副本）→ 调 `complete_json`。
2. 拿回 JSON 后用 **Pydantic 校验**；失败 → **把校验错误原文回喂**给模型，重试**一次**（`validate → 反馈 → 再校验`）。
3. 缺失字段（`None`/空）→ 进"缺失列表"返回，**绝不编造默认值**：
   - ❌ 错误做法：`cost_center` 缺就填 `"DEFAULT"`
   - ✅ 正确做法：报缺失，由 agent 生成反问（这叫 HITL 的雏形 —— 拿不准就问人）
4. 数量/金额边界：`qty>=1`、`qty<=100`（超出 → 校验失败 → 走纠错重试 → 仍失败则转人工）。**边界写进类型，不写进 if**。
5. 全过程落 trace（原始话、模型输出、校验结果、重试次数）。

**面试追问预演**
- "为什么不让模型直接输出最终 JSON 就信它？" → 三层护栏：**schema 约束 + 校验 + 纠错重试**；再叠加"缺失就问人"。模型的错误是概率性的，护栏必须是确定性的。
- "为什么要重试一次而不是三次？" → 一次纠错的成本收益最高（多数失败是格式问题）；再多说明**任务本身超出模型能力**，继续重试只是烧钱 —— 该转人工/换 pro 模型（这是路由策略的入口）。

## 2. `harness/tool_registry.py`（工具的"身份证 + 检查站"）

**职责**：注册工具、把工具描述喂给模型、并在**执行前**做参数校验。它不关心工具内部怎么实现。

**规格**
```python
@dataclass(frozen=True)
class Tool:
    name: str                      # 唯一，如 "create_purchase_flow"
    description: str               # 给模型看：什么时候用它（写"何时用"，不是"是什么"）
    parameters: dict               # JSON Schema（对象）
    executor: Callable[..., Any]   # 真正干活的函数（第一个参数是 state 或显式参数）
    side_effect: bool = False      # 有副作用的工具（建单/审批）标 True —— P0-03 的权限分级要用

class ToolRegistry:
    def register(self, tool: Tool) -> None          # 重名 → 直接报错（静默覆盖是事故源）
    def get(self, name: str) -> Tool                # 未注册 → ToolNotFound
    def schemas(self) -> list[dict]                 # 给 LLM 的工具清单（{"name","description","parameters"}）
    def invoke(self, name: str, args: dict, state: dict) -> dict
```

**`invoke` 的固定四步（顺序不许换）**
```
1) 查表：没这个工具 → {"ok": False, "error": {"code": "TOOL_NOT_FOUND", ...}}
2) 校验参数：缺必填/类型错 → {"ok": False, "error": {"code": "INVALID_ARGS", "detail": "..."}}
3) 执行：try/except 包住 → {"ok": True, "data": ...} / {"ok": False, "error": {...}}
4) 记录：tracer.record(...)（工具名、参数、耗时、成败）
```
**核心设计决策（面试会问）**：**工具出错返回结构化错误，而不是抛异常把 loop 炸掉。**
- 因为 loop 的天职是"根据观察结果再决策" —— **错误也是一种观察**：模型看到 `INVALID_ARGS: qty 必须是正整数` 能自己改参数重试。
- 但护栏在 agent 侧：同一工具连续失败 2 次 → 不再给它机会（见 agent.py）。

**自测**：注册 1 个假工具（`echo`），验证 ① 正常调用 ② 缺参数 ③ 未注册 ④ 执行体抛异常 → 四种都能返回**结构化**结果且 loop 不崩。

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
