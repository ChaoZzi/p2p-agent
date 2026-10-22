# P0-02 · 任务卡：`web/chat_api.py` + `main.py`（L0 入口层）

> 交付方：**dsh**（档位 `[dsh写+讲]`：要附《代码讲解》）
> 版本：v1.0（2026-10-22）　真源：`docs/architecture.md` §2.2、`docs/协作规范.md` v1.5、`docs/assets/p0-02-request-chain.html`（14 跳全链路）
> 你先读 `docs/进度.md` 的「当前状态」，再读本卡。**本卡不改任何 `[你敲]` 文件**。

---

## 0. 全局坐标（每段代码先看这个块）

```
① 浏览器/前端 ──POST /api/chat──► ② 【你写的】web/chat_api.py
                                      ├─ 装配 registry（两个工具）
                                      └─ agent.run(message, state, trace_id, registry)   ← 你唯一要调的下游
③ harness/agent.py（已写完，18 条验证全绿）────► registry.invoke ──► ④ agents/intent.py / tools/flow_client.py
                                                                        └─► Java :8000 /api/flows（已实现）
你现在写的是 ②：**入口 + 装配 + 存会话状态**这三件事，别的一行都不写。
```

**上游怎么调你（真实请求，逐字）**

```http
POST http://127.0.0.1:8001/api/chat
Content-Type: application/json

{"session_id": "s-42", "message": "成本中心填 CC-01"}
```

**你调下游的那一行（真名真签名，来自 `harness/agent.py:55`）**

```python
result = run(req.message, state=state, trace_id=trace_id, registry=REGISTRY)
# run(user_text: str, *, state: dict[str, Any], trace_id: str,
#     registry: ToolRegistry, max_rounds: int = MAX_ROUNDS) -> AgentResult
```

**一次请求里你被调用 1 次**（不是每轮一次）：`run()` 内部自己跑多轮 ReAct，你只在最外圈出现一次 —— 进来拿 state、出去存 state。

**真实样本（2026-10-22 我实跑，真 DeepSeek + 真 Java，不是编的）**

第 1 轮进来 `state={"session_id":"s-card","turn":1}` → 出去：

```json
{"status": "need_more_info",
 "reply": "已记录：采购 1 台 MacBook Pro，用途是给新来的前端使用。还差一个信息，即这笔费用记到哪个成本中心（cost_center）？请提供成本中心名称或编号，确认后我立即为你创建采购单。",
 "state": {"session_id": "s-card", "turn": 1,
           "trace_id": "8f0e…（本轮新生成的 uuid）",
           "intent": {"item": "MacBook Pro", "qty": 1, "reason": "给新来的前端用", "cost_center": null}},
 "tool_calls": [{"tool": "extract_intent", "args": {"text": "我想申请采购 1 台 MacBook Pro，给新来的前端用"}, "ok": true, "round": 1}]}
```

第 2 轮（`state` 就是上面那份原样带回来）→ `status=ok`，建单成功，Java 里真的多了一张单：
`{"tool":"create_purchase_flow","args":{"item":"MacBook Pro","qty":1,"reason":"给新来的前端用","cost_center":"CC-01"},"ok":true,"round":1}`，单号 `23e28513-b8cb-450f-9e05-001dcab11fd5`，`state=PENDING_APPROVAL`。

---

## 1. 业务定位

- **它是谁**（一句人话）：**前台接待员**。浏览器只跟它说话；它负责给这次对话发一个工牌号（`trace_id`）、把这位客人的档案袋（`state`）从柜子里取出来、把"会干活的师傅"（registry 里的两个工具）介绍给调度员（agent），最后把师傅的答复抄给客人、并把档案袋收回柜子。
- **在请求的哪一步**：整条链的**第一跳和最后一跳**（14 跳里的第 2 跳和第 14 跳）。前面只有浏览器，后面全是它调起来的下游。
- **坏了会怎样**：
  - 不存 `state` → 用户补答那一轮拿不到上一轮抽好的 item/qty/reason，模型重抽或重问，**多轮对话退化成一问一答**（这正是 2026-10-22 修掉的那个 bug，见 `docs/进度.md §已知问题`）；
  - 不复用 `trace_id` → Python 的 `trace/<id>.jsonl` 与 Java 的 `audit_log.trace_id` 对不上，线上出问题**无法按一次请求串起来排障**；
  - 把工具 executor 写错 → agent 收到 `ok=false`，模型只能道歉或乱试，**建单永远建不出来**。

---

## 2. 文件级改造清单（动手前先做这几件事）

| 动作 | 路径 | 说明 |
|---|---|---|
| 新建 | `python-service/web/__init__.py` | **空文件**（包标记，参考 `agents/__init__.py` 的约定） |
| 新建 | `python-service/web/chat_api.py` | 本卡主体：请求/响应模型 + registry 装配 + 会话存储 + 一个路由函数 `chat(req)` |
| 新建 | `python-service/main.py` | FastAPI 应用入口：`app = FastAPI()` + `app.include_router(...)` + `if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=8001)` |
| 改 | `python-service/pyproject.toml` | 加依赖 `fastapi`、`uvicorn`（**用 uv 装，不要 pip**，见下） |
| 不动 | `harness/agent.py`、`agents/intent.py`、`harness/tool_registry.py`、`tools/flow_client.py`、`tests/**` | 全部已就位/已被验证；有意见写进讲解，不要改 |
| 不建 | `web/` 下的任何 HTML/JS | 演示页是后面的卡；本卡只出 JSON API |

**装依赖（本机两个坑，别踩）**

```bash
cd /d/Projects/interview-project/p2p-agent/python-service
/c/Users/姜盛超/AppData/Local/hermes/bin/uv.exe add fastapi uvicorn    # uv 不在系统 PATH
# 装完确认：.venv 里没有 pip，所以永远别用 pip install；import 检查：
./.venv/Scripts/python.exe -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"
```

**端口**：Java 占 `8000`，Python 用 **`8001`**（`docs/architecture.md` §2.2 已定）。

**stub 先行还要不要**：本卡**不需要**做 stub —— 下游 `run()` 已经是真实现且 18 条验证全绿。你的第一版就应该能跑通真实链路；如果起步想先看形状，可以临时把 executor 换成 `lambda state=None, **kw: {"NOT_IMPLEMENTED": True}`，但**交付前必须换回真实现**。

---

## 3. 接口契约（逐字段：是什么 / 值从哪来 / 不填会怎样）

### 3.1 `POST /api/chat`

**请求体 `ChatRequest`**

| 字段 | 类型 | 是什么 | 值从哪来 | 不填 / 填错会怎样 |
|---|---|---|---|---|
| `session_id` | `str \| None` | 会话号：同一个人同一段对话的档案袋编号 | 浏览器从**上一轮响应**里拿到的 `session_id` 原样回传；**首轮不传** | 不传 → 服务端生成 `"s-" + uuid4().hex[:8]` 并在响应里回显，前端下一轮必须带回来；带了不存在的 id → 当作新会话（`state={}`），不报错 |
| `message` | `str`（必填，`min_length=1`） | 用户这一轮的原话 | 用户输入框 | 空串/全空格 → **400**，且**不许**调 `agent.run`（别浪费一次 LLM 调用） |

**响应体 `ChatResponse`**

| 字段 | 类型 | 是什么 | 值从哪来 | 填错会怎样 |
|---|---|---|---|---|
| `reply` | `str` | 给用户看的话（可能是反问） | `AgentResult.reply`，原样透传 | 自己加工/翻译 → 前端展示的和审计对不上 |
| `status` | `str` | 三态之一：`ok` / `need_more_info` / `give_up` | `AgentResult.status`，原样透传 | 自造第四种值 → 前端没法判断"该不该继续追问" |
| `session_id` | `str` | 本次会话号（可能是新生成的） | 步骤 2 的结果 | 不回显 → 前端下一轮拿不到档案袋，多轮断链 |
| `trace_id` | `str` | 本次请求的工牌号（uuid4） | 步骤 1 生成 | 不回显 → 页面无法做"按请求回放"（14 跳里的排障入口） |
| `tool_calls` | `list[dict]` | 本轮真正调过的工具摘要（`tool`/`args`/`ok`/`round`） | `AgentResult.tool_calls`，原样透传 | 自己裁剪成只留名字 → 页面侧栏看不到入参 |

**状态码**

| 场景 | HTTP | body |
|---|---|---|
| 正常（含 `status=give_up`：业务失败也是 200） | 200 | `ChatResponse` |
| `message` 为空 / 请求体不是 JSON | 400 | `{"error":{"code":"INVALID_REQUEST","message":"…"}}` |
| 服务端意外（装配错误、import 失败…） | 500 | `{"error":{"code":"INTERNAL","message":"…"}}`，**不要**把堆栈回给前端（日志里留） |

### 3.2 `GET /api/health`

返回 `{"status":"ok","sessions":<当前会话数>}` —— 给自检脚本和你自己判断"服务活着没"用，别做鉴权。

---

## 4. 编号逐步流程（`chat(req)` 里按这个顺序写）

| # | 做什么 | 调谁（真名真签名） | 值从哪来 | 失败怎么办 |
|---|---|---|---|---|
| 1 | 发工牌号 | `trace_id = new_trace_id()`（`tools.flow_client.new_trace_id`，`uuid4` 字符串） | 每次请求**新生成**，绝不复用上一轮的 | 不会失败 |
| 2 | 定会话号 | `session_id = req.session_id or ("s-" + uuid.uuid4().hex[:8])` | 请求体 / 自己生成 | — |
| 3 | 取档案袋 | `state = _sessions.get(session_id, {})`（**取副本**：`dict(...)`，别把存储里的对象直接交给 agent） | 上一步 | 没有 → 空 dict（首轮正常） |
| 4 | 调调度员 | `result = run(req.message, state=state, trace_id=trace_id, registry=REGISTRY)` | 上一步 + 模块级 REGISTRY | `run()` 承诺**永不抛**；真抛了 → 500 `INTERNAL`，日志留 trace_id |
| 5 | 存档案袋 | `_sessions[session_id] = dict(result.state)` | `AgentResult.state`（已含 `intent`、`trace_id`） | 加锁；超上限按"最久未用"淘汰（见 §6） |
| 6 | 组响应 | `ChatResponse(reply=result.reply, status=result.status, session_id=session_id, trace_id=trace_id, tool_calls=result.tool_calls)` | 步骤 1/2/4 | — |
| 7 | 返回 | `return resp` | — | — |

**跑完的结果**（自检口径）：HTTP 200；body 5 个字段齐全；`_sessions` 里多了这个 `session_id`；`trace/<trace_id>.jsonl` 出现 2–4 行（`agent.run` 每轮 1 行 + `tool.invoke` 每个工具 1 行 + 调了 `extract_intent` 时多 1 行 `intent.extract`）。

---

## 5. registry 装配（本卡唯一有"含金量"的部分，照这个写）

两个工具就是"师傅名单"：模型只能从这份名单里点人，参数由 `Tool.parameters`（JSON Schema）当场校验、不合法直接 `INVALID_ARGS` 回给模型。**这份 schema 既喂给模型、也用于校验** —— 两处必须是同一份对象，不许手写副本。

```python
import hashlib
from harness.tool_registry import Tool, ToolRegistry
from agents.intent import extract
from tools.flow_client import create_flow

def _exec_extract_intent(state=None, text=""):
    """工具①：调 agents/intent.py 抽结构化意图（无副作用）。"""
    intent, missing = extract(text, (state or {}).get("trace_id", ""))
    if intent is None:                    # 两次校验都没过 → 诚实告诉模型，别编
        return {"intent": None, "missing": [], "note": "意图抽取失败，请让用户重说"}
    return {"intent": intent.model_dump(), "missing": missing}

def _exec_create_purchase_flow(state=None, item="", qty=1, reason="", cost_center=""):
    """工具②：真的建单（有副作用）。request_id 由业务身份决定 —— 同一次输入重试拿到同一张单。"""
    request_id = "req-" + hashlib.sha1(
        f'{(state or {}).get("session_id")}|{item}|{qty}|{reason}|{cost_center}'.encode()
    ).hexdigest()[:8]
    return create_flow(request_id=request_id, item=item, qty=qty, reason=reason,
                       cost_center=cost_center, trace_id=(state or {})["trace_id"])

REGISTRY = ToolRegistry()
REGISTRY.register(Tool(
    name="extract_intent",
    description="把用户一句话抽成结构化采购意图；信息不全时返回缺失字段清单",
    parameters={"type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"]},
    executor=_exec_extract_intent))
REGISTRY.register(Tool(
    name="create_purchase_flow",
    description="用户信息齐全、要真正创建采购单时用（有副作用）",
    parameters={"type": "object",
                "properties": {"item": {"type": "string", "minLength": 1},
                               "qty": {"type": "integer", "minimum": 1, "maximum": 100},
                               "reason": {"type": "string", "minLength": 1},
                               "cost_center": {"type": "string", "minLength": 1}},
                "required": ["item", "qty", "reason", "cost_center"]},
    executor=_exec_create_purchase_flow,
    side_effect=True))
```

逐条说明（为什么这么写）：

- **executor 签名固定 `executor(state=..., **args)`**：`tool_registry.invoke` 是这么调的（`data = tool.executor(state=state, **args)`）。多一个位置参数就 `TypeError`，被 registry 兜成 `EXECUTOR_FAILED`。
- **`trace_id` 从 `state` 里取**，不要另生成：`run()` 已经把本轮的 `trace_id` 塞进 `work`，一个请求一个工牌，Java 侧才查得到对应的 `audit_log`。
- **`request_id` 用「session + 四个业务字段」算哈希**，不用随机数：同一张单重试必须命中 Java 的幂等（同 `request_id` 返回原单，我 2026-10-22 实测 `再次创建 → id 相同 = True`）。用随机 `request_id` 会把"重试"变成"下第二张单"。
- **`side_effect=True` 只给建单**：P0-03 的权限分级/审批门禁要靠这个字段；抽意图没有副作用。
- **不要 try/except `ContractError`**：让它冒到 registry → 记 `trace` + 回模型 `EXECUTOR_FAILED` + `detail`（含 Java 的 `code/message`），模型据 `detail` 决定补参数还是放弃。你在 chat_api 里再包一层只会把"Java 说 409 冲突"变成"服务端 500"。

---

## 6. 会话存储（内存 dict，够用且故意不复杂）

```python
_sessions: dict[str, dict] = {}          # session_id -> state（含 intent / trace_id / turn）
_sessions_lock = threading.Lock()
MAX_SESSIONS = 1000                       # 上限：演示机内存有限，且这是"不该无限长"的显式承诺
```

- **存什么**：`result.state` 的浅拷贝。里面已经有 `session_id` / `turn` / `trace_id` / `intent`。
- **为什么不用数据库/Redis**：这是单进程演示服务，`state` 丢失的最坏后果是"用户得重说一遍"；P0-03 若做多实例再换 Redis，本卡不提前引入基础设施。**这一点写进讲解**（面试会问"你为什么不持久化"）。
- **加锁**：FastAPI 线程池里多个请求会并发；`dict` 的读写在 CPython 里不会崩，但"取出来 → 改 → 写回"三步不加锁会互相覆盖。
- **上限淘汰**：超过 `MAX_SESSIONS` 时按插入顺序弹掉最早的（`next(iter(_sessions))`）。**不要**做后台定时清理线程。

---

## 7. 真实数据样本（成对给出，来源：2026-10-22 实跑）

### 7.1 入口 → 你的函数

```json
{"session_id": "s-card", "message": "成本中心填 CC-01"}
```

### 7.2 你交给 `agent.run` 的四个入参

| 参数 | 真实值 |
|---|---|
| `user_text` | `"成本中心填 CC-01"` |
| `state` | `{"session_id": "s-card", "turn": 1, "trace_id": "8f0e…上一轮那个", "intent": {"item": "MacBook Pro", "qty": 1, "reason": "给新来的前端用", "cost_center": null}}` |
| `trace_id` | 本轮新生成：`"138d0e2b-cff4-4a22-8737-87faf35d8d37"`（每轮都换，别用 state 里那个旧的） |
| `registry` | 模块级 `REGISTRY`，`schemas()` 就是 §5 那两份 JSON Schema |

### 7.3 `AgentResult`（第 2 轮，真实）

```json
{"reply": "采购单已创建成功：MacBook Pro ×1，事由“给新来的前端用”，成本中心 CC-01。单号 23e28513-b8cb-450f-9e05-001dcab11fd5，当前状态为待审批（PENDING_APPROVAL）。",
 "status": "ok",
 "tool_calls": [{"tool": "create_purchase_flow",
                 "args": {"item": "MacBook Pro", "qty": 1, "reason": "给新来的前端用", "cost_center": "CC-01"},
                 "ok": true, "round": 1}]}
```

### 7.4 `flow_client.create_flow` 从 Java 拿到的 body（真实，另一张单）

```json
{"id": "297cbf82-be41-414b-829c-2044e30dce9e", "request_id": "req-card-abf15bbb",
 "state": "PENDING_APPROVAL", "item": "MacBook Pro", "qty": 1,
 "reason": "新来的前端同事用", "cost_center": "CC-01",
 "created_at": "2026-10-22T12:34:10.009546700Z", "updated_at": "2026-10-22T12:34:10.016545900Z"}
```

> 这个 dict 就是工具② 的 `data`：模型在下一轮观察里看到 `{"ok":true,"data":{...,"id":"297cbf82-…","state":"PENDING_APPROVAL"}}`，所以它能在回复里说出单号。

### 7.5 落盘 trace（`trace/<trace_id>.jsonl`，同一请求）

第 1 轮（调了抽意图）4 行，第 2 轮（只建单）3 行：

```json
{"step":"agent.run","payload":{"round":1,"attempt":1,"ok":true,"action":"tool:extract_intent"}}
{"step":"intent.extract","payload":{"attempt":1,"ok":true,"missing":["cost_center"]}}
{"step":"tool.invoke","payload":{"tool":"extract_intent","ok":true,"side_effect":false,"duration_ms":0}}
{"step":"agent.run","payload":{"round":2,"attempt":1,"ok":true,"action":"final"}}
```

### 7.6 Java 侧对账（同一个 `trace_id`）

`GET /api/flows/{id}/audit` 返回 3 行，`trace_id` 列 = Python 这边那个 uuid：

```json
{"action":"FLOW_CREATED","actor":"system","trace_id":"138d0e2b-cff4-4a22-8737-87faf35d8d37","at":"…T12:34:10.013031200Z"}
{"action":"APPROVAL_REQUESTED","actor":"system","trace_id":"138d0e2b-…","at":"…T12:34:10.016545900Z"}
{"action":"SUBMITTED","actor":"system","trace_id":"138d0e2b-…","at":"…T12:34:10.016545900Z"}
```

> 注意后两行 `at` **完全相同**（已知问题，见 `docs/进度.md §已知问题` 的并列时间戳），按 `ORDER BY at` 查出来的顺序不保证 —— 这不是你的 bug，也不要顺手去改 Java。

---

## 8. 预期报错 / 中间态（正常的，别慌）

| 现象 | 是不是问题 | 怎么办 |
|---|---|---|
| `ModuleNotFoundError: No module named 'web'` | 缺 `web/__init__.py` | 补空文件 |
| `ValueError: duplicate tool name` | 模块级 `REGISTRY` 被注册两次（如在函数里重复装配） | 装配只在**模块级**执行一次 |
| `EXECUTOR_FAILED: TracePropagationError` | Java 没起 / 端口不对 / 没回显 `X-Trace-Id` | 先 `curl -H "X-Trace-Id: <uuid>" http://127.0.0.1:8000/api/flows/flow-seed-0001/audit` |
| `DeepSeekError: DEEPSEEK_API_KEY 未配置` | `python-service/.env` 没 key | 抄 `~/AppData/Local/hermes/.env` 里的 `DEEPSEEK_API_KEY`（该文件已 gitignore） |
| 200 但 `status=give_up` | **正常**：模型两次输出都不合法 / 同一工具连挂两次 | 前端按 `status` 展示"转人工"，不是 HTTP 错误 |
| 中文 curl 报 `Invalid UTF-8` | pwsh 把中文按 GBK 转给 curl.exe | 用 ASCII payload，或 `curl -d @body.json`（本机踩过） |

---

## 9. 完成自检清单（逐条勾）

- [ ] `./.venv/Scripts/python.exe -c "import fastapi, uvicorn"` 通过，`pyproject.toml` 里能看到这两个依赖
- [ ] `./.venv/Scripts/python.exe main.py` 起来后 `curl http://127.0.0.1:8001/api/health` → `{"status":"ok","sessions":0}`
- [ ] 首轮不带 `session_id`：`curl -X POST http://127.0.0.1:8001/api/chat -H "Content-Type: application/json" -d '{"message":"我想申请采购 1 台 MacBook Pro，给新来的前端用"}'` → 200，回显 `session_id`（形如 `s-3f9a1c02`）、`status=need_more_info`、`tool_calls[0].tool=extract_intent`
- [ ] 第二轮带上一步的 `session_id` + `{"message":"成本中心填 CC-01"}` → 200、`status=ok`、`tool_calls[0].tool=create_purchase_flow`，回复里含真实单号
- [ ] 拿这个单号查 Java：`curl -H "X-Trace-Id: <上面的 trace_id>" http://127.0.0.1:8000/api/flows/<单号>` → `state=PENDING_APPROVAL`
- [ ] `trace/<trace_id>.jsonl` 行数 = 3 或 4（见 §7.5）
- [ ] `message=""` → 400 `INVALID_REQUEST`，且日志里**没有** LLM 调用
- [ ] 连发两次同一个 `session_id` 的建单请求：Java 里**只有一张单**（`request_id` 哈希生效）
- [ ] `git status` 里只有 `web/`、`main.py`、`pyproject.toml`、`uv.lock` —— 没有动过 `[你敲]` 文件

---

## 10. 边界（不要做的事）+ 验收标准

**不要做**：

1. 不在 chat_api 里写业务判断（不查库存、不算金额、不判审批人）—— 那是 Java 的活；
2. 不改 `harness/agent.py`、`agents/intent.py`、`harness/tool_registry.py`、`tools/flow_client.py`、`tests/**`；
3. 不在响应里加自造字段（要加先提，我改契约并同步 `p0-02-request-chain.html` 与规格）；
4. 不把 `AgentResult` 序列化成 dict 再返回（直接建 `ChatResponse`，字段白名单就是契约）；
5. 不写测试文件 —— **独立验证测试由 Hermes 写**（`python-service/tests/test_chat_api_verify.py`，`test(verify):` 前缀），你只需让下面这些断言能过。

**Hermes 会写的验收断言（先告诉你，免得返工）**：

| # | 断言 |
|---|---|
| 1 | `POST /api/chat` 正常 → 200 且恰好 5 个字段（`reply/status/session_id/trace_id/tool_calls`），`status ∈ {ok, need_more_info, give_up}` |
| 2 | 首轮不带 `session_id` → 服务端生成并回显；响应里的 `session_id` 能在下一次请求里继续用 |
| 3 | **跨请求承接**：第 1 轮 `need_more_info` 之后，第 2 轮 `agent.run` 收到的 `state["intent"]` 与第 1 轮 `extract_intent` 抽出的内容一致（monkeypatch `complete_json`，不花钱） |
| 4 | `message=""` / 缺字段 → 400 且**没有**调用 `agent.run`（用替身计数） |
| 5 | `agent.run` 返回 `status=give_up` → HTTP 仍是 200（业务失败 ≠ HTTP 失败） |
| 6 | 每次请求的 `trace_id` 都是新的，且等于写进 `trace/<id>.jsonl` / 传给 `flow_client` 的那个 |
| 7 | 并发两个不同 `session_id` → 各自的 `state["intent"]` 不串味 |
| 8 | 两个工具的 `schemas()` 与 §5 完全一致（`name`/`required`/`qty` 上下界），`create_purchase_flow.side_effect is True` |
| 9 | 同一个 `session_id` 建单两次 → `flow_client.create_flow` 收到的 `request_id` **相同**（幂等） |
| 10 | 内部异常 → 500 `{"error":{"code":"INTERNAL"}}`，响应体里**不含**堆栈 |

**交付物**：代码 + `docs/task-cards/讲解-chat_api.md`（逐文件 1–3 句 + 关键决策：为什么不用 DB、为什么 request_id 用哈希、为什么锁 + 上限）+ 自测输出（§9 那几条 curl 的真实响应）。**不要 commit `[你敲]` 文件**，其余你自己 commit。
