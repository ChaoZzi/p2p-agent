# 数据模型 —— 现有三张表（P0-02）

> 真源：`java-service/src/main/resources/schema-sqlite.sql` 与 `schema-postgres.sql`
> （两版 DDL **完全相同**，PG 版文件末尾多一句 `REVOKE UPDATE, DELETE, TRUNCATE ON audit_log`）。
> 逐字段含义见下；表间关系见 §4。

## 1. 全景图

```
        请求的身份（request_id = "这次请求的身份证"）
   ┌───────────────────────────────────────────────┐
   │ idempotency  ← P0-01 的"回执柜"                 │
   │  request_id(PK) │ response_json │ trace_id │ created_at
   └───────────────────────────────────────────────┘
              ▲ request_id 同源（同一命名空间，用途不同）
              │
   ┌───────────────────────────────────────────────┐
   │ flow  ← 单据：一张单一行，**会被 UPDATE**        │
   │  id(PK) │ request_id(UNIQUE) │ state …          │
   └───────────────────────────────────────────────┘
              │ id  ←── flow_id（**逻辑**外键，无物理 FK 约束）
              ▼
   ┌───────────────────────────────────────────────┐
   │ audit_log  ← 流水：一张单多行，**只增不改**      │
   │  audit_id(PK) │ flow_id │ actor │ action │ detail │ trace_id │ at
   └───────────────────────────────────────────────┘
              │ trace_id
              ▼
   跨系统对账：Java 日志 [trace=…]  ↔  Python trace/<id>.jsonl
```

## 2. `flow` —— 单据（当前状态）

**一行 = 一张采购单**；会被 `UPDATE` 覆盖（只有"现在"，没有历史）。

| 字段 | 类型 / 约束 | 含义 | 值示例 | 谁负责填 |
|---|---|---|---|---|
| `id` | TEXT PRIMARY KEY | 单据的业务 id | `6f1c9e3a-…`（UUID） | `FlowService` 自己生成（**不用自增**：两库写法不同） |
| `request_id` | TEXT NOT NULL **UNIQUE** | **这次请求**的 id（幂等键） | `req-77a1` | HTTP 头 `X-Request-Id` → Controller → Service |
| `state` | TEXT NOT NULL | **当前**状态（`FlowState` 枚举名） | `PENDING_APPROVAL` | `FlowService`（条件更新时写入） |
| `item` | TEXT NOT NULL | 采购物品 | `键盘` | 建单入参 `CreateFlowCommand.item()` |
| `qty` | INTEGER NOT NULL | 数量 | `100` | 建单入参 |
| `reason` | TEXT NOT NULL | 采购理由 | `团队扩编` | 建单入参 |
| `cost_center` | TEXT NOT NULL | 成本中心（财务口径） | `CC-1001` | 建单入参 |
| `created_at` | TEXT NOT NULL | 建单时刻 | `2026-10-03T10:00:01.123Z` | `FlowService`（`Instant.now().toString()`） |
| `updated_at` | TEXT NOT NULL | **最后一次**状态变更时刻 | 同上格式 | `FlowService`（每次条件更新都改） |

> `state` 的取值权威定义在 Java：`engine/FlowState`（DRAFT / SUBMITTED / PENDING_APPROVAL / APPROVED / REJECTED / COMPLETED）。库里存的是**枚举名**。

## 3. `audit_log` —— 流水（历史，append-only）

**一行 = 这张单的一次变化**；只增不改（代码层没有 update/delete；PG 层被 `REVOKE`；业务层与状态变更同事务）。

| 字段 | 类型 / 约束 | 含义 | 值示例 | 谁负责填 |
|---|---|---|---|---|
| `audit_id` | TEXT PRIMARY KEY | 这条流水自己的编号 | UUID | `AuditService` 生成（**不用自增**） |
| `flow_id` | TEXT NOT NULL | **属于哪张单** | `6f1c9e3a-…` | `AuditService.append(...)` 的入参（逻辑指向 `flow.id`） |
| `actor` | TEXT NOT NULL | **谁**做的 | `system` / `zhangsan` | 系统动作 → `"system"`；人工动作 → `X-Operator` |
| `action` | TEXT NOT NULL | **做了什么** | `APPROVED` | `AuditAction` 枚举名（engine 侧定义） |
| `detail` | TEXT（可空） | **为什么 / 附注**（JSON 字符串） | `{"reason":"预算超了"}` | 调用方；没有内容传 `"{}"` |
| `trace_id` | TEXT（可空） | 这次请求的追踪号 | `tr-7f3a`（或 UUID） | `TraceFilter.currentTraceId()`（无请求上下文时兜底 `"unknown"`） |
| `at` | TEXT NOT NULL | **这条流水发生的时刻** | `2026-10-03T10:03:22.456Z` | `AuditService`（`Instant.now().toString()`） |

## 4. 表之间有什么关系（四条）

**① `flow.request_id` ⟷ `idempotency.request_id`：同一个命名空间，用途不同**
两者都是"这次请求的身份证"。但 `idempotency` 存的是**回执**（当时返回的响应体 `response_json`），`flow` 存的是**资源本身**。
- P0-01 的 mock 审批接口（没有自己的资源）→ 用 `idempotency` 表做幂等；
- P0-02 的建单（有 `flow` 这张资源表）→ 直接用 `flow.request_id` 的**唯一键**做幂等，更直接、还能靠数据库拦住并发重复建单。

**② `audit_log.flow_id → flow.id`：逻辑外键，但**刻意没有物理外键约束**
为什么不上 `FOREIGN KEY`：
- 两库共通子集优先（迁移/方言简单）；
- 审计表是 append-only，**不想被外键级联删除牵连** —— 审计的价值恰恰在于"即使单据被删/归档，历史仍在"。
代价：数据库不帮你保证引用完整性 → 靠应用层保证（只有 `AuditService` 能写，且必须传真实存在的 `flowId`）。

**③ `flow.state` 与 `audit_log.action`：不是外键，但是同一套词汇表的两种用法**
`state` 回答"**现在在哪**"，`action` 回答"**做过什么**"；最重要的是：**每一次状态迁移都同时产生两者**（改一次 `state` + 写一条 `action`）。
所以把 `audit_log` 的 action 序列按时间读一遍，就能**推出** state 的变化路径 —— 这正是"状态可由审计重放"（事件溯源方向）的雏形。

**④ `trace_id`：把三处跨系统串起来（不是表间关系，是"观测关系"）**
同一个 `trace_id` 同时出现在：
1. `audit_log.trace_id`（落库的审计行）
2. Java 日志每行的 `[trace=…]`（`TraceFilter` 放进 MDC）
3. Python 侧 `python-service/trace/<trace_id>.jsonl`（Python 生成并透传过来）
→ 跨语言排障不用猜时间戳：**一个号，三处对账**。

## 5. 演进留白（写在 schema 里但还没建）

| 未来要加的 | 什么时候 | 注意 |
|---|---|---|
| `flow_step`（每一步的明细/耗时） | P0-03 补预算/下单/付款主链路时 | 同样只写两份 schema 的**共通子集** |
| PG 专属能力（`JSONB` 存 detail、`timestamptz` 索引列） | 需要时 | **只能加在 `schema-postgres.sql`**，不能污染共通子集 |
| 双角色强隔离（migration 角色建表 / app 角色只有 INSERT+SELECT） | P1 | 现在 app 角色同时是 owner，理论上能把自己 GRANT 回来 —— 这是"机制"不是"绝对不可越权" |
| `audit_log` 加索引（`flow_id`、`trace_id`） | 数据量上来后 | 现在按 `flow_id` 查已经够快 |
