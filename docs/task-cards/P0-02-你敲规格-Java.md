# P0-02 「你敲」行为规格 — Java 侧（4 个文件）

> 角色：这 4 个文件是 `[你敲]` 档 —— **你亲手写**，Hermes 只给行为规格（细到能照着敲），写完由你 commit。
> 依据：`docs/task-cards/P0-02-min-loop.md §2/§4`（卡）、`docs/architecture.md §3`（状态机）、`docs/串讲.md`（主线）。
> 心法：**每个方法都问自己三句** —— 前置条件是什么？成功后世界变成什么样？失败时调用方拿到什么？

## 0. 通用约定（先读，三件都会影响你后面每一行）

| 约定 | 值 | 为什么 |
|---|---|---|
| 包结构 | 新代码放 `com.p2pagent.engine` / `.db` / `.web`（P0-01 的 `com.p2pagent.mockoa` 保持不动） | engine 是权威内核，不该塞进 mock 包 |
| **启动类要上移** | `MockOaApplication` 从 `com.p2pagent.mockoa` 移到 **`com.p2pagent`** | `@SpringBootApplication` 只扫描**自己所在包及子包**；不移，新包不会被扫到 → Bean 找不到（这是最经典的"改了会怎样"）。这一步可交给 dsh 做（机械改动） |
| 主键风格 | **全部用业务生成的字符串 id**（`UUID.randomUUID().toString()`） | SQLite/PG 的自增写法不同（`AUTOINCREMENT` vs `IDENTITY`），这是方言差异重灾区；业务 id 两边通用，也更适合跨服务传递 |
| 时间字段 | `TEXT` + ISO-8601 字符串（`Instant.now().toString()`） | 与 HTTP 契约里 `created_at` 是字符串一致；跨库零类型映射 |
| 并发安全 | 状态更新一律用**条件更新**（见 FlowService §3） | 两个审批人同时点"同意"，只能有一个成功 |

## 1. `engine/FlowState.java`（最短，先写它）

**职责**：状态的**权威字典**。只此一处定义"有哪些状态"。

**规格**
- `enum FlowState`，值：`DRAFT, SUBMITTED, PENDING_APPROVAL, APPROVED, REJECTED, COMPLETED`（P0-02 就这 6 个，P0-03 再加 `BUDGET_CHECK`/`PO_CREATED`/…）
- 提供一个 `boolean isTerminal()`：`COMPLETED` / `REJECTED` 返回 true，其余 false
- 提供一个 `static FlowState of(String raw)`：把库里存的字符串转成枚举，**非法值抛业务异常**（不要 `valueOf` 直接抛 `IllegalArgumentException` —— 那会变成 500）

**自测**：`isTerminal` 8 行够；顺手写 `of("DRAFT")` 与 `of("draft")`（大小写要不要容错？→ 建议**不**容错，库里存的就是枚举名，容错会掩盖写坏的数据）

## 2. `engine/FlowStateMachine.java`（全项目最核心的 30 行）

**职责**：唯一回答"从 A 能不能到 B"的地方。**纯函数、无状态、无 IO、无 Spring 注解**（这样才能被单测穷举）。

**规格**
```java
public final class FlowStateMachine {
    private static final Map<FlowState, Set<FlowState>> ALLOWED = /* 不可变表 */;
    public static boolean canTransit(FlowState from, FlowState to);
    public static Set<FlowState> nextStates(FlowState from);   // Set.of() 只读
    public static FlowState transit(FlowState from, FlowState to); // 非法 → 抛 409 FLOW_STATE_CONFLICT
}
```
**迁移表（照抄）**
| from | 允许的 to |
|---|---|
| DRAFT | SUBMITTED |
| SUBMITTED | PENDING_APPROVAL |
| PENDING_APPROVAL | APPROVED, REJECTED |
| APPROVED | COMPLETED |
| REJECTED | （无 —— 驳回修订留 P0-04） |
| COMPLETED | （无） |

**必须做到的三点**
1. **表驱动**，不写 `if/else` 链 —— 面试问"为什么"：可见性（一眼看全）、可测试（穷举 36 个组合）、可演进（P0-03 加状态只改表）。
2. 不可变表：`Map.of(...)` + `Set.of(...)`；`nextStates` 返回的集合**调用方改不动**。
3. 非法迁移的异常信息要**够排障**：把 from、to、以及"合法目标有哪些"都写进去（例：`cannot transit DRAFT -> APPROVED (allowed: [SUBMITTED])`）。这行字比报错码值钱。

**自测（写完立刻跑）**：两张表的每个组合 —— 合法迁移全部通过、非法迁移全部抛 409、`nextStates(COMPLETED)` 为空集。

## 3. `engine/FlowService.java`（编排层，把三样东西缝起来）

**职责**：建单 / 查询 / 审批 / 驳回的**事务编排**。它自己不判断"能不能迁移"（那是状态机的事），也不自己写审计（那是 AuditService 的事）。

**依赖注入**：`FlowRepository`（dsh 写，JdbcTemplate）、`AuditService`、`FlowStateMachine`（静态）、幂等（沿用 P0-01 的 `idempotency` 表）

**方法签名（钉死）**
```java
FlowView createFlow(String requestId, CreateFlowCommand cmd);  // 幂等
FlowView getFlow(String flowId);
FlowView approve(String flowId, String operator);
FlowView reject(String flowId, String operator, String reason);
```

**每个写方法的固定骨架（顺序不许换）**
```
1) 幂等/存在性检查（request_id 或 flow_id）
2) 读当前状态
3) 状态机校验（非法 → 409）
4) 条件更新状态（见下）
5) 写审计（同一事务）
6) 重新读出并返回视图
```
> 为什么这个顺序：**先把"能不能做"判死，再做，再记录**。反过来（先改再记）一旦审计写失败，世界就变成了"状态变了但没人知道为什么"。

**关键的并发设计：条件更新（乐观锁）**
- ❌ 不要：`SELECT state` → 内存判断 → `UPDATE ... SET state=?`。两个审批人同时点同意时，两次 SELECT 都可能读到 `PENDING_APPROVAL` → 两次都"合法" → 状态被推进两次（还会写出两条重复审计）。
- ✅ 要：`UPDATE flow SET state=?, updated_at=? WHERE id=? AND state=?`（**把"我以为的旧状态"写进 WHERE**）→ 看**影响行数**：1 = 我赢了；0 = 状态已被别人改过 → 抛 409。
- 这一条是 P0-01 幂等（`ON CONFLICT DO NOTHING` 看行数）的**同一个思想的第二次应用**：**让数据库来判断"我是不是第一个"**，不要靠应用层的时间差。

**createFlow 的内部动作**（对齐演示脚本：建单后状态是"待审批"）
```
幂等：idempotency 表里 request_id 已存在 → 直接返回原单（复用 P0-01 的写法）
否则：INSERT flow(state=DRAFT) → 审计 FLOW_CREATED
     迁移 DRAFT→SUBMITTED         → 审计 SUBMITTED
     迁移 SUBMITTED→PENDING_APPROVAL → 审计 APPROVAL_REQUESTED
```
（三次迁移各写一条审计 → 满足卡里"审计至少含创建/提交/审批/完成四类"的前三类）

**approve 的内部动作**：`PENDING_APPROVAL→APPROVED`（审计 APPROVED，actor=operator）→ `APPROVED→COMPLETED`（审计 COMPLETED）
**reject 的内部动作**：`PENDING_APPROVAL→REJECTED`（审计 REJECTED，detail 里带 reason）

**坑清单**
- 每个公开方法都要 `@Transactional`（**审计与状态变更必须同事务**）——否则"状态变了但审计没写" = 不可审计的系统
- 不要把 `HttpServletRequest` / 响应头塞进 service（分层）；`operator` 由 controller 从 `X-Operator` 取出后**作为参数传进来**
- 返回 `FlowView`（视图对象），不要直接暴露 repository 的行对象（DTO 边界）

**面试追问预演**
1. "两个人同时审批怎么办？" → 条件更新 + 影响行数 + 409（上面那套）
2. "为什么审计和状态变更要同事务？" → 否则系统会出现"状态变了，没人知道谁改的"
3. "幂等和乐观锁是不是一回事？" → 不是：幂等管"同一请求重复到达"，乐观锁管"不同请求竞争同一资源"；**实现手法相同（看 DB 的影响行数）**，这正是这个项目值得讲的地方

## 4. `engine/AuditService.java`（append-only）

**职责**：审计流水唯一的写入口 + 读出口。

**规格**
```java
public record AuditEntry(String auditId, String flowId, String actor, String action,
                        String detail, String traceId, String at) {}
public enum AuditAction { FLOW_CREATED, SUBMITTED, APPROVAL_REQUESTED, APPROVED, REJECTED, COMPLETED, REVISE_REQUESTED }

public class AuditService {
    public void append(String flowId, String actor, AuditAction action, String detail);
    public List<AuditEntry> listByFlow(String flowId);   // 按 at 升序
}
```
**四条硬规矩**
1. **接口上就没有 update/delete** —— append-only 的第一层保障是"代码里根本没有能改它的方法"（结构上禁止，而不是靠自觉）。
2. 每行都带 **`traceId`**（从 P0-01 的 `TraceFilter.currentTraceId()` 取）→ 审计与日志、与 Python 侧 trace 能对上。
3. `auditId` 自己生成（UUID），**不要自增**（方言差异，见 §0 约定）。
4. `detail` 存 JSON 字符串（P0-02 从简），但**至少**要能放下"谁、对哪张单、做了什么、理由"。

**面试追问（这题几乎必问）**："append-only 你怎么保证，不只是嘴上说？" → 分三层答：
- **代码层**：没有 update/delete 的 API 与 SQL（结构禁止）
- **数据库层**：PG 可 `REVOKE UPDATE, DELETE ON audit_log FROM <app_role>` → 越权修改**被数据库拒绝**（SQLite 做不到这层，只能靠代码层）
- **业务层**：审计与状态变更同事务；每次状态迁移必有一条审计 → 演进方向是**事件溯源**（状态可由审计重放得出）
> 三层分级的说法本身就是一个高分回答：它区分了"制度"和"机制"。

**自测**：一次完整流程（建单→同意）后 `select count(*) from audit_log` 应为 4 行（创建/提交/审批/完成），且 4 行 trace_id 与 Python 发出的那串一致。

---

## 5. 建议的写入顺序（每步都能自测，别一口气写完）

| 顺序 | 文件 | 写完立刻做的自测 |
|---|---|---|
| 1 | `FlowState` | 枚举 + `isTerminal` |
| 2 | `FlowStateMachine` | 穷举 36 组合（JUnit 或 main 里打表） |
| 3 | `AuditService` | 直接调 `append` 两次，查库有 2 行 |
| 4 | `FlowService` | 建单 → 查状态=PENDING_APPROVAL；审计 3 条；同意 → COMPLETED；审计 5 条 |

> 顺序不是随意的：**依赖方向**（状态机不依赖任何人，所以先写它）。这也是面试常问的"你怎么保证改动安全"的一个具体答案：先写无依赖的核心，再往外长。
