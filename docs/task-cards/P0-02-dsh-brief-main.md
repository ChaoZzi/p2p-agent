# P0-02 主体 执行简报（给 dsh）— 最小闭环：建单 → 审批 → 完成 → 审计

> 配套：`docs/task-cards/P0-02-min-loop.md`（卡，验收标准）、`docs/task-cards/P0-02-你敲规格-Java.md §0`（包结构与分档边界）、`docs/architecture.md §2.2/§3`（契约与状态机）
> 冲突时以**本简报**为准。设计责任：Hermes。执行：dsh。**做完每一阶段停下等 Hermes 审核。**

## 1. 为什么要分阶段（接口先行）

用户要**亲手写** 7 个核心文件，其中 `FlowService` 依赖你的 `FlowRepository`/DTO，Python 的 3 个文件依赖你的 `llm/deepseek.py` 与 `trace/tracer.py` 接口。所以**顺序不能乱**：

| 阶段 | 谁 | 内容 | 产出后可做什么 |
|---|---|---|---|
| **A** | dsh | 接口层 + 建表 + 套件修复（见 §2） | 用户可动笔写那 7 个文件 |
| **B** | 用户 | 7 个 `[你敲]` 文件 | — |
| **C** | dsh | `chat_api.py` / `main.py` / 静态演示页 + 端到端串联与自测 | 演示脚本可跑 |
| **D** | Hermes → 用户 | 审核 → 验收 | 进 P0-03 |

**你现在只做阶段 A，做完停下。** 不要顺手把用户的 `[你敲]` 文件写了（那是面试要讲的部分）。

## 2. 阶段 A 的具体任务

### A1. 回归套件：修编码 + 收录（独立 commit）
- 把套件从 `java-service/target/suite/` **先复制出来**（`mvn clean` 会清空 `target/`，上一轮它自己就丢过一次）。
- **修掉编码脆弱性**：现在脚本把含中文的 JSON 作为参数传给 `curl.exe`，Windows 原生命令参数按 ANSI(GBK) 转换 → Java 收到非法 UTF-8。**实证**：`malformed json body: Invalid UTF-8 start byte 0xbd`（`0xbd` 是「姜」的 GBK 首字节），表现为 `PASS=15 / FAIL=6`。
  - 修法二选一：① payload 全改 ASCII（`applicant=tester`）；② body 写成 UTF-8 文件、用 `curl -d @file`。
  - 再加一层保险：`scripts\run-regression.cmd` 包装（内部 `chcp 65001` + pwsh **绝对路径** `C:\Program Files\PowerShell\7\pwsh.exe` + `-NoProfile -File`），参数透传 `-BaseUrl` / `-Label`。
- **收录路径**：`scripts/p0-02-regression.ps1`（+ `scripts/pg-checks.ps1` 若也想收）；在 `README.md` 加一行用法。
- 验收：**同一个套件在 sqlite 与 postgres 两个 profile 下都 `PASS=21 FAIL=0`**，并且从 `cmd` 与 `git-bash` 两种 shell 各跑一次都全过（证明不再依赖环境编码）。

### A2. 三张表 + Java 接口层（独立 commit，本阶段最关键）
**schema（两份都要改：`schema-sqlite.sql` 与 `schema-postgres.sql`）**，沿用"共通子集"约束（`TEXT`/`PRIMARY KEY`/`UNIQUE`/`NOT NULL`，**不用自增、不用方言类型**）：

```sql
CREATE TABLE IF NOT EXISTS flow (
    id           TEXT PRIMARY KEY,          -- 业务 id（UUID 字符串），不用自增：方言差异重灾区
    request_id   TEXT NOT NULL UNIQUE,      -- 与 idempotency 表同源，创建接口用它做幂等
    state        TEXT NOT NULL,             -- FlowState 枚举名，权威定义在 Java
    item         TEXT NOT NULL,
    qty          INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    cost_center  TEXT NOT NULL,
    created_at   TEXT NOT NULL,             -- ISO-8601 字符串（与 HTTP 契约一致）
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id  TEXT PRIMARY KEY,             -- UUID，自增是方言差异
    flow_id   TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,                -- 见 AuditAction（用户手写）
    detail    TEXT,
    trace_id  TEXT,
    at        TEXT NOT NULL
);
-- PG 侧追加（SQLite 无权限模型，靠应用层只提供 INSERT 接口等效约束）：
-- REVOKE UPDATE, DELETE ON audit_log FROM p2p_agent;
```
> `flow_step` 表**本卡不建**（卡里 §4 只列了 flow/audit_log/idempotency），留 P0-03。

**Java 接口层**（`com.p2pagent.engine` / `.db` / `.web`，新包）：

| 文件 | 内容 |
|---|---|
| `api/dto/FlowView.java` | record：`id, requestId, state, item, qty, reason, costCenter, createdAt, updatedAt`，字段线上名 **snake_case**（`@JsonProperty`，同 P0-01 的约定） |
| `api/dto/CreateFlowCommand.java` | record：`item, qty, reason, costCenter`（`request_id` 走参数/头，不在这里） |
| `api/dto/AuditView.java` | record：`auditId, flowId, actor, action, detail, traceId, at` |
| `db/FlowRepository.java` | JdbcTemplate：`insertFlow(FlowView)` / `findById(String)` / **`updateStateIfCurrent(String id, FlowState expected, FlowState next, String updatedAt)` → int（影响行数）** / `findByRequestId(String)` |
| `db/AuditRepository.java` | `insert(AuditView)` / `listByFlow(String flowId)`（**只有 INSERT 与 SELECT**，无 update/delete —— append-only 的结构性保障） |
| `web/FlowController.java` | 4 个端点：`POST /api/flows`（幂等：`X-Request-Id` + body.request_id 校验沿用 P0-01）／`GET /api/flows/{id}`／`POST /api/flows/{id}/approve`（`X-Operator`）／`POST /api/flows/{id}/reject`（body 带 `reason`）／`GET /api/flows/{id}/audit` |
| `error/ErrorCodes.java` | 补 `FLOW_STATE_CONFLICT`（409）、`FLOW_NOT_FOUND`（404） |

**Controller 只做三件事**：取头（`X-Operator` / `X-Request-Id`）、调 `FlowService`（用户手写）、映射异常到统一错误体。**不要**在 Controller 里做状态判断。

> 若此时 `FlowService` 还不存在：用最小接口/桩让它能编译（`interface FlowService` + 空实现），并在交付说明里明确标注"待用户实现"。**不要**替用户实现它。

### A3. Python 接口层（独立 commit）
| 文件 | 内容 |
|---|---|
| `llm/deepseek.py` | `complete_json(system, user, schema, *, model="flash") -> dict`：JSON mode + 超时 + 重试一次；key 从 `python-service/.env` 读 `DEEPSEEK_API_KEY`（不入库，`.gitignore` 已覆盖） |
| `trace/tracer.py` | `record(trace_id, step, payload) -> None`：写 `trace/<trace_id>.jsonl`（一行一个 JSON） |
| `tools/flow_client.py` | Java 契约客户端：`create_flow / get_flow / approve / reject / get_audit`，自动带 `X-Trace-Id` 与 `X-Request-Id`；**沿用 P0-01 `oa_client.py` 的 trace 回显断言写法** |

### A4. 两处机械改动（独立 commit）
1. **启动类上移到扫描根（重要）**：`MockOaApplication` 现在在 `com.p2pagent.mockoa`，而 `@SpringBootApplication` 只扫描**它自己所在包及子包** → 用户阶段 B 写的 `com.p2pagent.engine.*`（`@Service`）**根本不会被扫到**，症状是启动后报 "no qualifying bean"。做法：把类移到 `java-service/src/main/java/com/p2pagent/MockOaApplication.java`（包名 `com.p2pagent`）；IDEA 里 `Refactor → Move` 会连带更新引用。**注意保留** `java-service/data` 目录创建的既有行为（见下条）。
2. **`data/` 目录创建改成仅 sqlite profile**：现在无条件建（PG profile 下留一个空目录，无害但脏）。
   - 验收：PG profile 启动后 `java-service/data/` 不被创建；sqlite profile 启动后行为与现在一致。

## 3. 契约要点（钉死，别自创）

- **状态机**：`DRAFT → SUBMITTED → PENDING_APPROVAL → APPROVED → COMPLETED`；`PENDING_APPROVAL → REJECTED`。非法迁移 → **409 `FLOW_STATE_CONFLICT`**。
- **建单语义**：`POST /api/flows` 成功后状态应为 **`PENDING_APPROVAL`**（内部依次迁移并各写一条审计：`FLOW_CREATED` / `SUBMITTED` / `APPROVAL_REQUESTED`）。
- **审批语义**：`approve` → `PENDING_APPROVAL→APPROVED`（审计 `APPROVED`，actor=`X-Operator`）→ `APPROVED→COMPLETED`（审计 `COMPLETED`）；`reject` → `REJECTED`（审计 `REJECTED`，detail 带 reason）。
- **状态更新必须用条件更新**（乐观锁，用户会照规格实现，仓储层必须提供这个方法）：
  `UPDATE flow SET state=?, updated_at=? WHERE id=? AND state=?`，**影响行数 0 → 抛 409**。
- **错误体形状**沿用 P0-01：`{"error":{"code","message","trace_id"}}`。
- 所有写路径沿用 P0-01 的 `TraceFilter`（日志带 `[trace=...]`），审计行也要落 `trace_id`。

## 4. 验收（阶段 A 部分）

- [ ] A1：修好的套件在 **sqlite 与 postgres 两个 profile** 下 `PASS=21 FAIL=0`，且 **cmd 与 git-bash 两种 shell 各跑一次都全过**
- [ ] A2：三张表（`flow`/`audit_log`/`idempotency`）在两个库都由各自 `schema-*.sql` 建出；PG 侧 `REVOKE` 生效（**实测**：`UPDATE audit_log ...` 应被拒绝、`INSERT` 允许 —— 贴 psql 原始输出）
- [ ] A2：4 个端点全部可用（`POST/GET/approve/reject/audit`），非法迁移返回 409（贴 curl 输出）
- [ ] A3：`llm/deepseek.py` 被真调一次（`DEEPSEEK_API_KEY` 从 `.env` 读取，**key 不入库**）；`tracer.record` 落出 JSONL 样例行
- [ ] A4：PG profile 启动后 `java-service/data/` 不再被创建
- [ ] 讲解：`docs/task-cards/P0-02-主体-讲解.md` 覆盖"为什么先交接口层""条件更新与幂等的关系""审计 append-only 的三层保障（代码/数据库权限/同事务）"

## 5. 硬纪律（同前）

- **不要写这 7 个 `[你敲]` 文件**：`engine/FlowState.java`、`engine/FlowStateMachine.java`、`engine/FlowService.java`、`engine/AuditService.java`、`agents/intent.py`、`harness/tool_registry.py`、`harness/agent.py`。需要它们时用最小桩并标注。
- 不动 `docs/` 其它文件、`.idea/`、`.gitignore`；密码只允许占位符；一个可演示增量一个 commit（`feat(java):` / `feat(python):` / `fix(scripts):` / `docs:`）。
- 卡住就停下说清卡在哪，**不要伪造证据**，也不要用替代品（H2 等）糊过去。
