# P0-01 施工说明（给 dsh 的执行简报）

> 本文件 = `docs/task-cards/P0-01-smoke.md` 的**字段级补充**，由 Hermes 确认契约后产出。
> 给 dsh 用：把本文件整篇作为会话开场指令。卡以 `P0-01-smoke.md` 为准，**字段、版本、路径以本文件为准**（有冲突时本文件优先，因为它写得更细）。
> 产出日期：2026-09-19　设计责任：Hermes

---

## 0. 你的角色与硬纪律

- 你是 **dsh**：只负责**编码执行**。需求/架构/审核由 Hermes 做，用户负责验收与 commit 把关。
- **只写 P0-01 范围内的文件**。不要动 `docs/`（除新增 `docs/contracts/java-service-openapi.json` 与你的《讲解.md`）、不要动 `.idea/`、不要动 `.gitignore`、不要写 README 之外的东西。
- 交付物 = 代码 + **`docs/task-cards/P0-01-讲解.md`**（逐文件讲清"这段代码做什么、为什么这么写、改了会怎样"）+ **自测证据**（真实命令输出，贴进讲解.md）。
- **commit 纪律**：一个可演示增量 = 一个 commit，message 用 `feat:` / `fix:` / `docs:` 前缀。只 commit 你负责的文件。（P0-01 全部是 dsh 档，没有 `[你敲]` 文件，用户只做 review + 合入。）
- 交付后**停下来等 Hermes 审核**，不要自己接着做 P0-02。

## 1. 环境事实（已由 Hermes 配好并实测，别再折腾）

| 项 | 值 |
|---|---|
| JDK | 21（`C:\Program Files\Java\jdk-21`） |
| Maven | 3.9.9，用全路径 `D:\software\maven-dist\apache-maven-3.9.9\bin\mvn.cmd`（**不要**用 git-bash 里的 `mvn`，MSYS 包装器会报 classworlds ClassNotFound） |
| 依赖源 | `~/.m2/settings.xml` 已配 `https://maven.aliyun.com/repository/public`（2026-09-19 已修，原 HTTP 老入口被 Maven http-blocker 封杀） |
| 代理 | **不要走系统代理**（127.0.0.1:7897 会掐断 maven 系域名 TLS）。Maven/Java 默认直连，不要去配 `<proxies>` |
| start.spring.io | **不可达** → 手写最小 `pom.xml` |
| Python | 3.11.16 + uv 0.12.7（`uv init` / `uv add` 用起来） |
| DeepSeek key | P0-01 **不需要**（无 LLM 调用）；P0-02 才用，从 `~/AppData/Local/hermes/.env` 取 `DEEPSEEK_API_KEY` 写进 `python-service/.env` |

## 2. 版本基线（已实测可拉取，别自创版本）

- `spring-boot-starter-parent` **3.5.16**（离线兜底：本地缓存已有 3.4.2 全套，若网络抽风就退 3.4.2 并在讲解里说明）
- `<java.version>21</java.version>`（= javac `--release 21`，**必须 21**：`--release 17` 会连 `Thread.ofVirtual()` 这类 API 一起锁死）
- `org.xerial:sqlite-jdbc:3.53.4.0`（已预热进本地缓存）
- `org.springdoc:springdoc-openapi-starter-webmvc-ui` 2.8.x（Boot 3.5 兼容线，具体小版本取镜像最新）
- `spring-boot-starter-web` / `-jdbc` / `-test`

## 3. 契约字段（**钉死，不许改**）

### 3.1 透传与幂等规范

- Python 侧生成 `trace_id` = **标准 UUID4 小写带横线字符串**，放在请求头 `X-Trace-Id`。
- **Java 必须**：① 响应头回显 `X-Trace-Id`；② 该次请求的每行日志带 `[trace=<uuid>]`；③ 把 trace_id 与请求一起落进幂等表/记录（P0-01 只需 mock-oa 写端点的记录里有它）。
  → 若请求没带 `X-Trace-Id`，Java 自己生成一个 UUID 并按上面三条处理（便于直接 curl 调试）。
- Java 幂等：以 `request_id` 为唯一键。**同一 `request_id` 重复请求 → 返回首次结果（HTTP 200 + 响应头 `X-Dedup: true`）**，不新建记录。
- JSON 字段命名统一 **snake_case**（Java DTO 用 record + `@JsonProperty`；Python 天然一致）。

### 3.2 Java 端点（服务跑 :8000）

| 端点 | 请求 | 响应 / 语义 |
|---|---|---|
| `GET /healthz` | — | `200 {"status":"ok"}`，回显 `X-Trace-Id` |
| `POST /api/mock/oa/approvals` | header `X-Trace-Id`、`X-Request-Id`（幂等键，缺失则 422）<br>body `{"request_id":"...","flow_id":"...","title":"...","amount":0,"applicant":"..."}` | `200 {"approval_id":"oa-<uuid8>","status":"PENDING","created_at":"<ISO8601>","dedup":false}`<br>重复 request_id → 同样的 body 但 `dedup:true` + header `X-Dedup: true` |
| `PUT /api/mock/faults` | `{"timeout_ms":5000,"random_delay":false}` | `200` 当前开关状态；`timeout_ms>0` 时 mock 端点 sleep 该毫秒（上限 10000）。`{"timeout_ms":0}` 关闭 |
| `GET /api/mock/faults` | — | `200 {"timeout_ms":0,"random_delay":false}` |
| `GET /v3/api-docs` | — | springdoc 自动生成（用于导出契约文件） |

**校验与错误体**（所有错误统一这个形状，含 trace）：
```json
{"error":{"code":"VALIDATION","message":"request_id is required","trace_id":"<uuid>"}}
```
- 缺必填字段 → `422` + `code: "VALIDATION"`
- 状态机非法流转（P0-01 暂无状态机，留位）→ `409` + `code: "FLOW_STATE_CONFLICT"`

**持久化**：用 SQLite 落地幂等表（既满足"幂等落地"，又验证 sqlite-jdbc 驱动可用，为 P0-02 去风险）：
- 库文件 `java-service/data/mock-oa.db`（`*.db` 已被 gitignore）
- 表 `idempotency(request_id TEXT PRIMARY KEY, response_json TEXT NOT NULL, trace_id TEXT, created_at TEXT NOT NULL)`
- schema 走 `src/main/resources/schema.sql` + `spring.sql.init.mode=always`
- 注意并发：`PRIMARY KEY` + 插入冲突时读回原记录（`INSERT OR IGNORE` 后 `SELECT`）

### 3.3 Python 侧（P0-01 只做最小，页面留到 P0-02）

- `python-service/`：`pyproject.toml`（uv 管理，依赖 `httpx` + `pytest`）
- `python-service/tools/oa_client.py`：
  - `create_approval(*, request_id, flow_id, title, amount, applicant, base_url="http://127.0.0.1:8000") -> dict`
  - 自建 `trace_id`（uuid4）→ 请求头 `X-Trace-Id`、`X-Request-Id=request_id`
  - **断言**（不满足就抛异常，不许静默通过）：
    1. 响应头 `X-Trace-Id` == 自己生成的 trace_id
    2. 首次调用 `dedup == false`；同 request_id 二次调用 `dedup == true`
  - 提供可跑的 self-test：`uv run python -m tools.oa_client`（或 `--selftest`）打印两端证据
- **透传证据**：Python 侧打印 trace_id → 到 Java 控制台日志里 grep 同一个 trace_id → 两边原始输出都贴进讲解.md（这是本卡最核心的验收证据）

## 4. 验收（7 条，全过才算交付）

1. `mvn -f java-service/pom.xml spring-boot:run` 能起在 **:8000**，`curl :8000/healthz` 返回 ok
2. mock-oa 三个端点（创建/查故障/设故障）都可用
3. 故障开关 `PUT /api/mock/faults {"timeout_ms":5000}` 生效（curl 实测耗时 ≥5s）
4. `docs/contracts/java-service-openapi.json` 已导出入库（springdoc）
5. Python `tools/oa_client.py` 调通 mock-oa，且 **trace_id 透传断言通过**
6. `README.md` 有占位（项目名 + 一句话 + 两条启动命令），`.gitignore` 已覆盖（Hermes 已补 `out/`，你不用改）
7. 幂等语义在 `POST /api/mock/oa/approvals` 落地（同 request_id 第二次返回 `X-Dedup: true`）

## 5. 交付格式

```
feat(java): mock-oa 冒烟服务（契约/透传/幂等/故障开关）   ← commit
feat(python): oa_client 与 trace 透传断言                ← commit
docs: P0-01 讲解.md（逐文件 + 自测证据）                  ← commit
```

`docs/task-cards/P0-01-讲解.md` 必须覆盖：
- 每个文件 1–3 句：作用、关键设计决策、改了会怎样
- trace_id 从 Python 到 Java 的完整链路（贴两端原始输出）
- 幂等表为什么用 `INSERT OR IGNORE` + `SELECT`（并发下会发生什么）
- 为什么 pom 里 `java.version=21`（提示：`--release` 同时锁 API）
- `@JsonProperty` 保 snake_case 的理由（跨语言契约一致性）

## 6. 失败预案

任一步卡壳 > 2 天 → 按 `PRD.md §4` 降级：Java 侧砍到只剩 mock-oa + 幂等，主链路先 Python 内直连。**卡住就停下来说清楚卡在哪，不要硬猜、不要伪造自测输出。**
