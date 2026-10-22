# L0 入口层讲解 — `web/chat_api.py` + `main.py`（P0-02 主体 · L0）

> 任务卡：`docs/task-cards/P0-02-dsh-任务卡-chat_api.md`（v1.0）
> 交付：`python-service/web/__init__.py`、`web/chat_api.py`、`main.py`、`pyproject.toml`（+ `uv.lock`）、本文
> 未动：`harness/agent.py`、`agents/intent.py`、`harness/tool_registry.py`、`tools/flow_client.py`、`tests/**`
> 证据：§4 全部是 2026-10-22 本机实跑输出（真 DeepSeek + 真 Java），原样粘贴。

---

## 0. 这一层在链路里的位置

14 跳里的**第 2 跳**（浏览器 → 我）和**第 14 跳**（我 → 浏览器）。一次 HTTP 请求里本模块只被调用一次：

```text
浏览器 --POST /api/chat--> [web/chat_api.py] --agent.run(...)--> harness/agent.py（内部自己跑多轮 ReAct）
                              |-- 装配 registry（两个工具）        |-- registry.invoke --> agents/intent.py / tools/flow_client.py --> Java :8000
                              |-- 取/存会话 state <-----------------┘
```

它只做三件事：**装配**（把两个工具介绍给调度员）、**存会话**（档案袋取出来 / 放回去）、**收发**（校验请求、按字段白名单组响应）。
业务判断一行都没有 —— 查库存、算金额、判审批人都是 Java 的事。

---

## 1. 逐文件讲解

### 1.1 `web/__init__.py`（空文件）

包标记，与 `agents/__init__.py` 的约定一致（0 字节）。**为什么必须有**：`main.py` 里写的是 `from web import chat_api`，
没有它、且解释器从别的目录启动时，`web` 会退化成命名空间包甚至 import 失败 —— 任务卡 §8 里那条 `ModuleNotFoundError: No module named web` 就是它。

### 1.2 `web/chat_api.py`（本卡主体，约 300 行）

**① 请求 / 响应模型（`ChatRequest` / `ChatResponse`）**

- `ChatRequest.message` 写成 `str | None` 而**不是**必填 `str`：FastAPI 对"必填字段缺失"会回 **422 + 它自己的错误体**，而契约要求 **400 + `{"error":{"code":"INVALID_REQUEST"}}`**。把字段放宽、自己校验，400/422 的口径就只由我们决定。
- `ChatResponse` 恰好 5 个字段，`status` 用 `Literal["ok","need_more_info","give_up"]`：三态是契约的一部分，写成字面量后它会出现在 OpenAPI 里，谁要加第四种值都得先改这里。
- **改了会怎样**：把 `ChatResponse` 换成 `dict`/`AgentResult` 直接返回，`tool_calls` 里就会混进 agent 内部字段（比如 `state`），前端与验收脚本看到的形状每次都可能变 —— 字段白名单就是契约。

**② 统一错误体（`error_response`）**

`{"error":{"code","message"}}`，与 Java 侧同一个形状（方便两边共用前端错误处理）。**不含堆栈**：堆栈只进日志。

**③ 会话存储（`_sessions` + `_take_state` / `_put_state` / `session_count`）**

- 取的时候 `dict(...)` 复制一份：不把存储里的对象直接交给 agent（`run()` 会往里写 `trace_id` / `intent`，不复制就会污染存储）。
- 存的时候整体加锁，并在超过 `MAX_SESSIONS=1000` 时淘汰最早的。
- **改了会怎样**：不加锁 → 并发两个请求"取 → 改 → 写回"互相覆盖，表现为"我明明补答了成本中心，下一轮它又问一遍"；不复制 → 更隐蔽，`state` 变成单例。

**④ 两个工具（`_exec_extract_intent` / `_exec_create_purchase_flow` + 模块级 `REGISTRY`）**

- 装配**只在模块级做一次**：`ToolRegistry.register` 对重名直接抛错，放在函数里每次请求装配一次会立刻 `duplicate tool name`。
- schema **一份对象两用**：既通过 `registry.schemas()` 喂给模型，又由 `ToolRegistry._validate_args` 用来校验 —— 不写副本，避免"提示里的约束"和"校验的约束"漂移。
- executor 签名固定 `executor(state=..., **args)`（registry 就是这么调的）；多一个位置参数会被兜成 `EXECUTOR_FAILED`。
- **`trace_id` 从 `state` 取**，不另生成：`run()` 已经把本轮的 uuid 写进 `work`，一个请求一个工牌，Java 的 `audit_log` 才对得上。
- **`request_id` 用「session_id + 四个业务字段」算 sha1 前 8 位**，不用随机数（见 §3.2）。
- `side_effect=True` 只给建单：P0-03 的权限分级/审批门禁要靠它。
- **不 try/except `ContractError`**：让它冒到 registry → 记 trace + 回模型 `EXECUTOR_FAILED`（含 Java 的 code/message），模型据此补参数或放弃。在 chat_api 再包一层只会把"Java 说 409"变成"服务端 500"。

**⑤ 两个端点（`GET /api/health`、`POST /api/chat`）与七步顺序**

`chat()` 的注释里标了 ①~⑦，与任务卡 §4 一一对应：发工牌号 → 校验 message → 定会话号 → 取档案袋 → 调 `run()` → 组响应 → 存档案袋。
两个刻意的顺序：

1. **`message` 校验在 `run()` 之前** —— 空串直接 400，一次 LLM 都不会花（§4 证据 7 的日志里只有 `chat rejected`，没有 `chat start`）；
2. **先组响应、再存 state** —— 组响应失败（比如 status 不合约）时不会留下"回了用户但没存上下文"的半截状态。

**⑥ 校验异常处理器（`install_validation_handler`）**

FastAPI 的 `RequestValidationError`（body 不是合法 JSON / 字段类型不对）默认 422；这里统一转成 400 + 我们的错误体。
它装在 `main.create_app()` 里，而不是 chat_api 模块级 —— 因为异常处理器属于 **app** 对象。

### 1.3 `main.py`

`create_app()` 装配（`FastAPI` + `include_router(chat_api.router)` + 装校验处理器），模块级 `app = create_app()` 给 uvicorn 用，
`__main__` 里配日志并 `uvicorn.run(app, host="127.0.0.1", port=8001)`。
**为什么拆一个 `create_app()`**：测试要一个干净实例（或不带异常处理器的对照）时不用依赖 import 副作用；生产仍走模块级 `app`。
**改了会怎样**：把 8001 写成 8000 → 直接和 Java 抢端口；把 `install_validation_handler` 删掉 → 非法 JSON 退回 422，契约里那行 400 就失效了。

### 1.4 `pyproject.toml`（+ `uv.lock`）

新增 `fastapi>=0.159.1`、`uvicorn>=0.54.0`（用 uv 装，`.venv` 里仍然没有 pip）。顺手把过时的 description 从"P0-01 只有 mock-oa 客户端"改成"L0 入口 API + Agent Harness + 企业系统工具客户端"。
**改了会怎样**：手写版本号写死 → 与 `uv.lock` 打架；`uv sync` 会按 pyproject 重解，锁文件跟着变。

---

## 2. 契约落地对照

| 契约 | 落在哪 | 怎么保证 |
|---|---|---|
| `session_id` 不传就生成并回显 | `chat()` ③ | `("s-" + uuid4().hex[:8])`；不存在的 id 当新会话（`_take_state` 返回 `{}`），不报错 |
| `message` 为空 → 400 且不调 LLM | `chat()` ② | 校验在 `run()` 之前；log 只有 `chat rejected` |
| 正常/`give_up` 都是 200 | `chat()` 返回 `ChatResponse` | 只有 400/500 是 HTTP 错误，业务失败走 `status` |
| body 不是 JSON → 400 | `install_validation_handler` | `RequestValidationError → 400 INVALID_REQUEST` |
| 服务端意外 → 500 且不含堆栈 | `chat()` 的 `except Exception` | `log.exception` 落日志、响应只给 `code/message` |
| 响应恰好 5 字段 | `ChatResponse` | pydantic 模型即白名单（§4 证据 1/2 的字段列表） |
| 每次请求新 `trace_id` | `chat()` ① `new_trace_id()` | 绝不复用 state 里的旧值 |
| `GET /api/health` | `health()` | `{"status":"ok","sessions":N}` |

---

## 3. 三个关键决策（面试会问的三个"为什么不"）

### 3.1 为什么不用数据库 / Redis 存会话

这是**单进程演示服务**：`state` 丢了最坏后果是"用户得把这句话重说一遍"，没有资损、没有不可逆动作。用一个 Redis 换来的收益在这个阶段是 0，代价是：多一个要装的中间件、多一套连接/超时/序列化配置、演示机多一个进程要盯着。
**边界说清楚**：`uvicorn` 默认单 worker，内存态没问题；一旦上多 worker / 多实例，会话就会"这台认得、那台不认得" —— 那时换 Redis（或把 state 放进数据库）是**因为出现了真实约束**，而不是因为"早晚要用"。

### 3.2 为什么 `request_id` 用哈希而不是随机数

`request_id` 是**幂等键**：Java 的 `/api/flows` 对同一个 `request_id` 返回**原来那张单**。

| 做法 | 同一次意图重试时 | 后果 |
|---|---|---|
| 随机 uuid | 两次请求 = 两个不同的 key | 下**两张单**（这不是重试，是重复下单） |
| `sha1(session + item + qty + reason + cost_center)` | 同一个 key | Java 返回原单，重复点击/网络重试都安全 |

为什么把 `session_id` 也算进去：不同会话里的同一笔采购是**两件业务**（不同的人/不同的场景），不能被当成重复。
§4 证据 9 里同一 `request_id` 连建两次拿到**同一个 flow id**，`flow` 总数不变。

### 3.3 为什么加锁 + 上限 + 不做后台清理

- **加锁**：FastAPI 把同步端点丢进线程池，多个请求会真并发。"取出来 → 改 → 写回"三步不加锁会互相覆盖（丢更新），加锁是最小成本的正确性。
- **上限 1000**：这是"会话不该无限长"的显式承诺 —— 没有上限的 dict 就是内存泄漏。
- **按插入顺序淘汰**：任务卡 §6 写的就是 `next(iter(_sessions))`，我按它实现。**严格说这不是 LRU**：Python dict 再次赋值不会把 key 移到末尾。要真 LRU 得用 `OrderedDict.move_to_end` —— 但演示机 1000 条上限下，"最早创建的先淘汰"够用且更好解释。
- **不做后台清理线程**：定时器是这里最不需要的复杂度（线程要管生命周期、要和请求抢锁、退出还要 join）。淘汰发生在写入路径上，一次 O(1) 的 pop。

---

## 4. 自测证据（真实输出，逐条对应任务卡 §9）

```text

=== 0) 环境 ===
python: 3.11.16
fastapi/uvicorn: 0.159.1 0.54.0

=== 1) GET /api/health ===
{"status":"ok","sessions":1}

=== 2) 第 1 轮（无 session_id）：POST /api/chat ===
request  : {"message":"我想申请采购 1 台 MacBook Pro，给新来的前端用"}
response : {"reply":"已确认：采购 1 台 MacBook Pro，用途是给新来的前端用。还需要你补充一下费用归属的**成本中心（cost_center）**，例如「研发部-前端组 CC1024」。补齐后我就为你创建采购单。","status":"need_more_info","session_id":"s-748c0c4d","trace_id":"0b8f070d-5f46-477d-8f20-35865dcf08a7","tool_calls":[{"tool":"extract_intent","args":{"text":"我想申请采购 1 台 MacBook Pro，给新来的前端用"},"ok":true,"round":1}]}

=== 3) 第 2 轮（带 session_id）：POST /api/chat ===
request  : {"session_id":"s-748c0c4d","message":"成本中心填 CC-01"}
response : {"reply":"采购单已提交成功：MacBook Pro × 1，用于「给新来的前端用」，成本中心 CC-01。单号 req-6ab8c4ec，当前状态为待审批（PENDING_APPROVAL），后续可凭 request_id 查询进度。","status":"ok","session_id":"s-748c0c4d","trace_id":"ee9376b0-361a-4ec7-9a2f-20a584466059","tool_calls":[{"tool":"create_purchase_flow","args":{"item":"MacBook Pro","qty":1,"reason":"给新来的前端用","cost_center":"CC-01"},"ok":true,"round":1}]}

=== 4) 拿单号查 Java：GET /api/flows/{id} ===
{"id":"d820c0c9-20c7-455b-94ff-8b24d997df7e","request_id":"req-6ab8c4ec","state":"PENDING_APPROVAL","item":"MacBook Pro","qty":1,"reason":"给新来的前端用","cost_center":"CC-01","created_at":"2026-10-22T12:51:51.759228300Z","updated_at":"2026-10-22T12:51:51.765152300Z"}

=== 5) Java 审计（同一个 trace_id） ===
[{"audit_id":"dabf712d-3873-404f-88bb-d5c77550daad","flow_id":"d820c0c9-20c7-455b-94ff-8b24d997df7e","actor":"system","action":"FLOW_CREATED","detail":"{\"item\":\"MacBook Pro\",\"qty\":1}","trace_id":"ee9376b0-361a-4ec7-9a2f-20a584466059","at":"2026-10-22T12:51:51.762145600Z"},{"audit_id":"06bc0706-7a27-4b5b-bd06-fe9fecc177c5","flow_id":"d820c0c9-20c7-455b-94ff-8b24d997df7e","actor":"system","action":"SUBMITTED","detail":"{}","trace_id":"ee9376b0-361a-4ec7-9a2f-20a584466059","at":"2026-10-22T12:51:51.765152300Z"},{"audit_id":"73a1e758-87c0-4fa7-8494-c9db387abb9d","flow_id":"d820c0c9-20c7-455b-94ff-8b24d997df7e","actor":"system","action":"APPROVAL_REQUESTED","detail":"{}","trace_id":"ee9376b0-361a-4ec7-9a2f-20a584466059","at":"2026-10-22T12:51:51.766150200Z"}]

=== 6) trace 落盘行数（第1轮 4 行 / 第2轮 3 行） ===
trace/0b8f070d-5f46-477d-8f20-35865dcf08a7.jsonl  lines=4
trace/ee9376b0-361a-4ec7-9a2f-20a584466059.jsonl  lines=3

=== 7) message 为空 -> 400 ===
HTTP/1.1 400 Bad Request
{"error":{"code":"INVALID_REQUEST","message":"message must not be empty"}}

=== 8) 请求体不是合法 JSON -> 400 ===
HTTP/1.1 400 Bad Request
{"error":{"code":"INVALID_REQUEST","message":"invalid request body: body.16 JSON decode error"}}

=== 9) 幂等双建（同 request_id -> 同一张单，flow 总数不变） ===
first  id: d820c0c9-20c7-455b-94ff-8b24d997df7e state: PENDING_APPROVAL
second id: d820c0c9-20c7-455b-94ff-8b24d997df7e state: PENDING_APPROVAL
same flow id (Java idempotency): True

total_flows: 5

=== 10) 会话数（health） ===
{"status":"ok","sessions":1}
```

对应关系：

| 任务卡 §9 | 证据 |
|---|---|
| 依赖可 import | 证据 0（fastapi 0.159.1 / uvicorn 0.54.0） |
| `main.py` 起来后 health | 证据 1（`{"status":"ok","sessions":1}`） |
| 首轮不带 session_id → need_more_info + extract_intent | 证据 2（`s-748c0c4d`） |
| 第二轮带 session_id → ok + create_purchase_flow + 真实单号 | 证据 3（`req-6ab8c4ec`） |
| 拿单号查 Java → PENDING_APPROVAL | 证据 4（`d820c0c9-…`） |
| `trace/<trace_id>.jsonl` 3 或 4 行 | 证据 6（4 行 / 3 行） |
| `message=""` → 400 且无 LLM 调用 | 证据 7（日志侧见 §5 第 4 条） |
| 同 session 建单两次 → 只有一张单 | 证据 9（同 flow id，`total_flows: 5` 不变） |
| 跨端同一个 trace_id | 证据 5（3 行审计的 `trace_id` = 第 2 轮的 uuid） |

---

## 5. 与任务卡的差异 / 待 Hermes 复核

| # | 差异 | 为什么这么做 | 若不要怎么改 |
|---|---|---|---|
| 1 | 多了一个 `create_app()`，`app = create_app()` | 测试要干净实例时不必依赖 import 副作用 | 直接在模块级建 `app`，测试 `import main` 即可（功能不变） |
| 2 | `chat()` 往 `state` 里补了 `session_id` 与 `turn`（`turn = 旧值 + 1`） | 任务卡 §7.2 的真实样本里 `state` 有 `session_id` / `turn`，但 §4 的七步没写这一步；不补的话样本对不上，且 `turn` 也没人维护 | 删掉两行（`state` 里就只剩 agent 写的 `trace_id` / `intent`） |
| 3 | `ChatResponse.status` 用 `Literal` 三态 | 契约显式化 + OpenAPI 可见；`run()` 只会产出这三个值 | 改成 `str`（但"第四种值"就没人拦了） |
| 4 | `message` 做成可选 + 手动校验，并加了 `install_validation_handler` | 契约要 400，而 FastAPI 对必填/类型错误默认 422 | 改成必填 `str`，接受 422（与契约冲突，不建议） |
| 5 | `_exec_create_purchase_flow` 里 `trace_id` 缺失时抛 `RuntimeError("trace_id missing in state …")` | 只有绕过 `run()` 直接调工具才会触发；抛一句能看懂的话，比 `KeyError: trace_id` 好排障（都会被 registry 兜成 `EXECUTOR_FAILED`） | 改回 `(state or {})["trace_id"]` |
| 6 | 顺手更新了 `pyproject.toml` 的 `description` | 原文还写着"P0-01 只有 mock-oa 客户端"，已过时 | 改回原文 |

另外报告两个**环境事实**（不是代码差异）：

- 任务卡里的 uv 路径 `C:\Users\姜盛超\AppData\Local\hermes\bin\uv.exe` **已经不存在**（环境迁移过）。实际可用的是 `D:\DevCache\hermes\bin\uv.exe`（uv 0.12.7）。本次用它执行 `uv add fastapi uvicorn`。
- `fastapi 0.159` 的 `include_router` 不再把子路由平铺进 `app.routes`，而是挂一个 `_IncludedRouter` —— 所以"打印 `app.routes` 看不到 /api/*"是正常的，路由是生效的（证据 1/2 已证明）。

---

## 6. 已知边界

- **内存态会话**：重启即清空；单进程内有效（`uvicorn` 默认 1 worker）。多实例要换 Redis/PG —— 这是 P0-03 之后的决定，不提前引。
- **浅拷贝的前提**：`_take_state` 用 `dict(...)`。`agent.run` 里是 `work["intent"] = ...` 这种**重绑定**，不会原地改嵌套 dict，所以够用；哪天 agent 改成 `work["intent"]["x"] = ...` 就必须换 `deepcopy`。
- **没有鉴权 / 限流 / 请求体大小限制**：这是本地演示服务，不暴露公网；`/api/health` 也刻意不鉴权（自检脚本要用）。
- **没有 CORS**：演示页后续由同一个 FastAPI 提供（同源），暂不需要；若页面另起端口，要加 `CORSMiddleware`。
- **日志只记 `trace_id`/`session_id`/`turn`/工具名，不记 message 原文**（隐私最小化）；需要原文时去 trace JSONL 里看（`intent.extract` 那一步记了）。

---

## 7. 环境坑（本轮新增）

1. **uv 路径变了**：见 §5 说明。
2. **PowerShell 函数名 `H` 会撞内置别名 `h`（Get-History）**：我第一版证据脚本用它做标题，输出里全是 `Get-History: 无法绑定参数 Id`。别名优先级高于函数 —— 改名 `Section` 解决。（这条也解释了为什么脚本里到处是 `& curl.exe`：避免调用被别名劫持。）
3. **中文 body 仍然要走 `-d @file`**：`curl --data-binary "@r1.json"`，文件用 UTF-8 写。直接把中文塞进命令行参数会被 pwsh 按 GBK 转给 curl.exe，Java 侧报 `Invalid UTF-8`。
4. **`python main.py` 必须在 `python-service` 目录下跑**：`sys.path[0]` 是脚本目录，`web` / `harness` / `tools` 这些裸包才找得到（与 `uv run python -m …` 同理）。

---

## 8. 复现命令

```bash
# 1) Java 侧（另开窗口，:8000）
scripts\run-java.cmd

# 2) Python 侧（:8001）
cd python-service && ./.venv/Scripts/python.exe main.py

# 3) 自检
curl -s http://127.0.0.1:8001/api/health
#    首轮（中文 body 写文件再发，避免命令行编码）
curl -s -X POST http://127.0.0.1:8001/api/chat -H "Content-Type: application/json" --data-binary "@r1.json"
#    第二轮：把上一步的 session_id 填进 r2.json
curl -s -X POST http://127.0.0.1:8001/api/chat -H "Content-Type: application/json" --data-binary "@r2.json"
```

---

## 9. 对照 Hermes 的 10 条验收断言（实现位置）

| # | 断言 | 本实现的位置 |
|---|---|---|
| 1 | 200 且恰好 5 字段，status ∈ 三态 | `ChatResponse`（Literal 保证） |
| 2 | 首轮无 session_id → 生成并回显，可续用 | `chat()` ③ + `_take_state/_put_state` |
| 3 | 跨请求承接：第 2 轮 `state["intent"]` 与第 1 轮一致 | `_take_state` 取回 + `_put_state` 存 `result.state`（agent 在 `run()` 里把 `intent` 写进 `work`） |
| 4 | `message=""` / 缺字段 → 400 且没调 `run` | `chat()` ②；缺字段走 `install_validation_handler` → 400（注意：`message` 缺失时它给的是 400，不是 422） |
| 5 | `give_up` → 仍 200 | `chat()` 不做 status→HTTP 的映射，一律 200 |
| 6 | 每次新 trace_id，且等于写进 trace / 传给 flow_client 的那个 | `chat()` ① 生成 → `run(trace_id=...)` → `work["trace_id"]` → registry/flow_client 都用它 |
| 7 | 并发两个 session 不串味 | `_sessions` 按 key 隔离 + 取副本 + 加锁 |
| 8 | 两个工具的 schema 与 §5 一致，`create_purchase_flow.side_effect is True` | 模块级 `REGISTRY.register(...)` 的那两个 `Tool(...)` |
| 9 | 同 session 建单两次 → `request_id` 相同 | `_exec_create_purchase_flow` 的 sha1（证据 9） |
| 10 | 内部异常 → 500 `{"error":{"code":"INTERNAL"}}`，无线索堆栈 | `chat()` 的 `except Exception` + `error_response` |

> 对 Hermes 写测试的提示：`run` / `create_flow` / `extract` 在本模块里都是**模块级名字**，`monkeypatch.setattr(chat_api, "run", fake)` 即可替换；
> 测试建议用 `from main import create_app; app = create_app()` 拿实例，这样非法 JSON → 400 的契约也能一并验到。
