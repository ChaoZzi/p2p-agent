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

## 0.5 这四个文件在业务里各干什么（**业务视角 —— 读任何一节之前先看这里**）

**业务背景一句话**：员工跟数字员工说"帮我采购 100 个键盘"，系统要**建单 → 提交 → 请求审批 → 审批 → 完成**，而且**每一步都要留下改不掉的记录**（谁、什么时候、对哪张单、做了什么、为什么）。下面这四段代码就是这套流程的"发动机"。

| 文件 | 业务上它是谁 | 一次请求里什么时候用它 | **它坏了，业务上会怎样**（记住这个比记签名有用） |
|---|---|---|---|
| `FlowState` | **状态字典** —— 一张单业务上有几种状态（草稿/已提交/待审批/已批准/已驳回/已完成） | 每次从库里读出一张单，把 `"PENDING_APPROVAL"` 这种**字符串**翻译成程序能判断的对象 | 状态认不出来 → 单子卡死；或者被当成另一种状态处理（**把已完成的单当草稿重新提交**） |
| `FlowStateMachine` | **规则书** —— 从 A 能不能走到 B | 每次要改状态**之前**问它一句 | **最危险的一个**：没有它，一张已完成的单可以被重新提交、被重复审批 |
| `AuditService` | **留痕员 / 黑匣子** —— 把"谁在什么时候对哪张单做了什么"写进 `audit_log`，**只增不改** | 每一次状态变化，旁边都跟着写一条 | 状态变了但**没人知道是谁改的** → 系统不可审计、无法追责（企业软件红线） |
| `FlowService` | **调度员** —— 把上面三样缝起来，在一个事务里完成"校验 → 改状态 → 留痕" | 每个 HTTP 请求进来都走它 | 顺序乱了（先改状态后写审计）→ 出现"状态变了但没人知道为什么" |

**一次请求走下来，这几个零件被碰到的顺序**：

```
POST /api/flows                        建单
  └─ FlowService.createFlow
       ├─ 状态机：DRAFT → SUBMITTED                    ┐
       ├─ 状态机：SUBMITTED → PENDING_APPROVAL         │ 连续三次迁移
       └─ 审计（AuditService）：FLOW_CREATED           │ 写 3 条
                              SUBMITTED                │
                              APPROVAL_REQUESTED       ┘
       └─ 返回 FlowView（给 Python 侧 / 前端看）

POST /api/flows/{id}/approve           审批同意
  └─ FlowService.approve
       ├─ FlowState.of(库里读出的字符串)   ← 翻译成枚举才能判断
       ├─ 状态机：PENDING_APPROVAL → APPROVED → COMPLETED
       ├─ 审计（AuditService）：APPROVED / COMPLETED   ← 再写 2 条
       └─ 返回 FlowView
```

→ 所以一次"**建单 + 同意**"跑完，`audit_log` 里应该有 **5 行**（创建 / 提交 / 请求审批 / 批准 / 完成），而且这 5 行的 `trace_id` 都和这次请求**同一串** —— 这就是"可追"的证据。

> **怎么用这一节**：读下面任何一节（§1–§4）时，先回到这张表确认"**我写的这个东西在哪一步被用到、它坏了会怎样**"，然后再去看签名和规则 —— 顺序反过来就容易"看不懂我在写什么"。

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

**三个方法的分工（v1.1 补充 —— 上一版只给了签名，没说"为什么要有三个"，这是文档的缺陷）**

同一张表，三种"问法"，服务三种不同场景：

| 方法 | 它在回答什么 | 什么时候用 | 返回什么 |
|---|---|---|---|
| `canTransit(from, to)` | **"能吗？"** 是/否 | 需要**先判断再决定**的地方：给前端算"按钮该不该亮"、演示脚本打表、测试穷举 | `boolean` |
| `nextStates(from)` | **"能去哪？"** 有哪些选择 | 展示 / 日志 / 文档；也是 `canTransit` 的实现基础 | 只读 `Set`；**没有出边 → 空集**，不是 null |
| `transit(from, to)` | **"去做，不合法就报错"** | **业务主路径**：真的要推进状态时（`FlowService` 里每一处迁移） | 合法 → 返回 **`to`**；非法 → 抛 409 |

**为什么有了 `canTransit` 还要有 `transit`**：因为"先判断、不合法就抛异常"这段代码会在**每个调用点重复出现**，而且**很容易漏** —— 有人写了 `if` 却忘了处理 else，非法状态就会被**静默推进**（这是最危险的一类 bug：世界变了，但没人知道它不该变）。`transit` 把"校验 + 失败即报错"封装成一个**绕不过去的动作**：调用方只写一行，语义就是"我要把它推进到 X"，而错误消息只有这一处维护。

**`transit` 为什么有返回值（返回 `to`）**：便于链式赋值，让状态推进的代码读起来就是一句人话。

```java
// FlowService.createFlow 里（建单要连推两步）
FlowState s = FlowState.DRAFT;
s = FlowStateMachine.transit(s, FlowState.SUBMITTED);          // → SUBMITTED
s = FlowStateMachine.transit(s, FlowState.PENDING_APPROVAL);   // → PENDING_APPROVAL

// FlowService.approve 里
FlowState current = FlowState.of(view.state());
FlowState next = FlowStateMachine.transit(current, FlowState.APPROVED); // 非法 → 409（消息带 from->to）
repository.updateStateIfCurrent(flowId, current, next, now);           // current 就是"我以为的旧状态"
```

**一句话对照**：`canTransit` 是**问句**（能吗），`nextStates` 是**清单**（有哪些），`transit` 是**命令句**（去做，做不到就报错）。

**`transit` 的参考实现（4 行）**
```java
public static FlowState transit(FlowState from, FlowState to){
    if (!canTransit(from, to)){
        throw ApiException.conflict("cannot transit " + from + " -> " + to
                + " (allowed: " + nextStates(from) + ")");
    }
    return to;
}
```
（`ApiException.conflict` = 409 + `FLOW_STATE_CONFLICT`，见 `mockoa/error/`）
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

### §3.0 动手前先做这三件事（**改造清单 —— v1.1 补充，别跳过**）

你打开 `engine/FlowService.java` 会看到一个**接口**（阶段 A 的占位），`engine/FlowServiceStub.java` 是个**临时桩**（四个方法都抛 501）。你的任务是**把它们换成一个真实的实现类**。

**① 把接口改成类**

```java
// 改之前（阶段 A 的占位接口：只有声明，没有方法体）
public interface FlowService {
    FlowView createFlow(String requestId, CreateFlowCommand cmd);
    FlowView getFlow(String flowId);
    FlowView approve(String flowId, String operator);
    FlowView reject(String flowId, String operator, String reason);
}

// 改之后（你的实现类）
@Service
public class FlowService {
    // ...
}
```
（`@Service` 的 import：`org.springframework.stereotype.Service`）

**② 注入两个依赖（构造器注入，与 dsh 的 Controller / 仓储写法一致）**

```java
private final FlowRepository flowRepository;   // 读写 flow 表（dsh 已写好）
private final AuditService auditService;       // 写审计（你刚写的）

public FlowService(FlowRepository flowRepository, AuditService auditService) {
    this.flowRepository = flowRepository;
    this.auditService = auditService;
}
```
为什么用构造器注入：依赖能声明成 `final`（不可变）；启动期就能发现"依赖找不到/循环依赖"；单测里可以 `new FlowService(假仓储, 假审计)` 直接造。字段注入（`@Autowired` 写在字段上）只能靠反射塞值。
（你现在的 `AuditService` 用的是字段注入 —— 可以顺手改成构造器注入保持一致，也可以先放着，我 review 时再提。）

**③ 删除 `engine/FlowServiceStub.java`** —— 整个文件删掉，不是注释掉

它的注释里就写着"阶段 B 请删除本文件"。不删会撞上下面两种报错之一：

| 你的状态 | 看到的报错 | 含义 |
|---|---|---|
| stub 还在，而你的类也是 `@Service` | 启动报 `expected single matching bean but found 2` | 同一个接口有两个 Bean，Spring 不知道该注入哪个 |
| 接口已改成 class，stub 里 `implements FlowService` 还在 | 编译报 `FlowService is not an interface` | stub 引用的东西已经不是接口了 |

**⚠️ 心理预期：这三件事要一次做完**

中间任何一步单独完成时，工程都是**红的**（编译不过或启动失败）—— **这是正常的，别以为自己写错了**：
- 只删 stub、还没实现 → `FlowService` 没有实现类，Controller 注入失败
- 只改 class、没删 stub → 报上面表格里的错

**省事技巧**：stub 里那四个方法的骨架（`@Override` + 参数名）**直接复制过来当起点**，只把方法体换成 §3.1 的真逻辑 —— 签名不用重新敲。

**开工检查清单**
- [ ] `FlowService.java` 已改为 `@Service public class FlowService`
- [ ] 构造器注入了 `FlowRepository` + `AuditService`
- [ ] `@Service` 的 import 已加
- [ ] `FlowServiceStub.java` 已删除
- [ ] 四个公开方法带 `@Transactional`（`getFlow` 只读，可以不加）

### §3.1 逐步业务流程（**v1.1 —— 照这个敲，不用你猜**）

**你手上的零件（真名真签名）**

| 零件 | 方法 | 作用 |
|---|---|---|
| `flowRepository`（dsh 已写） | `findByRequestId(String)` → `Optional<FlowView>` | 幂等查单 |
| | `findById(String)` → `Optional<FlowView>` | 按 id 查单 |
| | `insertFlow(FlowView)` | 插一行 |
| | `updateStateIfCurrent(String id, Enum<?> expected, Enum<?> next, String updatedAt)` → `int` | **条件更新**，返回影响行数（1=我赢了 / 0=别人先改了） |
| `auditService`（你刚写的） | `append(String flowId, String actor, AuditAction action, String detail)` | 写一条审计流水 |
| `FlowStateMachine`（你写的） | `transit(from, to)` | 非法迁移 → 抛 409 |
| `FlowState`（你写的） | `of(String raw)` | 库里的字符串 → 枚举 |

**三个"值"的约定**
- `now` = `Instant.now().toString()`（**每次写库前重新取一次**，用于 `updated_at`）
- `actor`：系统自动动作一律 `"system"`；人工动作 = 方法入参 `operator`（由 Controller 从 `X-Operator` 取出后传进来）
- `detail`：审计附注，JSON 字符串；没有内容就传 `"{}"`

#### 方法 1：`createFlow(requestId, cmd)` —— 建单（幂等）

```
步骤 1  幂等查单
        Optional<FlowView> existed = flowRepository.findByRequestId(requestId);
        有值 → 直接 return 它（不改状态、不写审计，整个请求就是"重复了"）
        没值 → 继续

步骤 2  建单（状态 = DRAFT）—— 用「幂等插入」，不是普通 insert
        FlowView view = new FlowView(
            UUID.randomUUID().toString(),   // id          ← 自己生成（不用数据库自增：两库写法不同）
            requestId,                      // requestId   ← 方法入参
            FlowState.DRAFT.name(),         // state       ← 枚举名（库里存字符串）
            cmd.item(),                     // item        ← 入参命令
            cmd.qty(),                      // qty         ← 入参命令
            cmd.reason(),                   // reason      ← 入参命令
            cmd.costCenter(),               // costCenter  ← 入参命令
            now,                            // createdAt   ← 此刻
            now);                           // updatedAt   ← 此刻（刚建出来两者相同）

        int inserted = flowRepository.insertFlowIfAbsent(view);   // ← 幂等插入：返回影响行数
        if (inserted == 0) {
            // 0 = 已经有人用这个 requestId 建过了（两个并发请求都查到"不存在"）
            //     → 返回原单，不推进状态、不写审计（整个请求到此结束）
            return flowRepository.findByRequestId(requestId)
                    .orElseThrow(() -> ApiException.notFound("flow not found after conflict: " + requestId));
        }
        // inserted == 1 → 这张单确实是我建的，继续往下

        auditService.append(view.id(), "system", AuditAction.FLOW_CREATED,
                            "{\"item\":\"" + cmd.item() + "\",\"qty\":" + cmd.qty() + "}");

> **v1.2 修正（重要，别照旧版写）**：本步骤原来写的是"`insertFlow` + `catch (DataIntegrityViolationException)` 再查一次"。
> 那个写法**在 PostgreSQL 上会直接炸**：唯一键冲突会让**整个事务进入 aborted 状态**，catch 之后的查询会报
> `current transaction is aborted, commands ignored until end of transaction block`（SQLite 没这问题 —— 所以**只测 SQLite 会漏掉这个坑**）。
> 现在改用 `flowRepository.insertFlowIfAbsent(view)`（内部是 `INSERT ... ON CONFLICT(request_id) DO NOTHING`），
> 走"**不抛异常**"的路：**两个数据库行为一致，事务也不被污染** —— 与 P0-01 的幂等写法同一个思想。

步骤 3  DRAFT → SUBMITTED
        3a. FlowStateMachine.transit(FlowState.DRAFT, FlowState.SUBMITTED);      // 非法 → 409（新建单不可能非法，属防御性一致）
        3b. int rows = flowRepository.updateStateIfCurrent(view.id(), FlowState.DRAFT, FlowState.SUBMITTED, now);
            rows == 0 → throw ApiException.conflict("state changed while submitting flow " + view.id());
        3c. auditService.append(view.id(), "system", AuditAction.SUBMITTED, "{}");

步骤 4  SUBMITTED → PENDING_APPROVAL（同样三小步）
        4a. transition(SUBMITTED, PENDING_APPROVAL)
        4b. updateStateIfCurrent(view.id(), FlowState.SUBMITTED, FlowState.PENDING_APPROVAL, now) → 0 → 409
        4c. auditService.append(view.id(), "system", AuditAction.APPROVAL_REQUESTED, "{}");

步骤 5  重新读出并返回（返回值必须是库里的真相，不是你手里的想象）
        return flowRepository.findById(view.id())
                .orElseThrow(() -> ApiException.notFound("flow vanished: " + view.id()));
```
**跑完的结果**：状态 `PENDING_APPROVAL`，审计 **3 行**（FLOW_CREATED / SUBMITTED / APPROVAL_REQUESTED）。

#### 方法 2：`getFlow(flowId)` —— 查单
```
步骤 1  return flowRepository.findById(flowId)
                .orElseThrow(() -> ApiException.notFound("flow not found: " + flowId));   // 404
```
（只读，**不需要** `@Transactional`）

#### 方法 3：`approve(flowId, operator)` —— 审批通过（连推两步）
```
步骤 1  读单 + 翻译状态
        FlowView current = flowRepository.findById(flowId).orElseThrow(() -> ApiException.notFound("flow not found: " + flowId));
        FlowState from = FlowState.of(current.state());        // 库里字符串 → 枚举

步骤 2  PENDING_APPROVAL → APPROVED
        2a. FlowStateMachine.transit(from, FlowState.APPROVED);        // from 不是 PENDING_APPROVAL → 409（消息带 from->to/allowed）
        2b. int rows = flowRepository.updateStateIfCurrent(flowId, from, FlowState.APPROVED, now);
            rows == 0 → throw ApiException.conflict("approve raced: flow " + flowId + " is not " + from + " anymore");
            ★ 这一步就是并发的正面战场：两个审批人同时点"同意"，只有一个能拿到 1，另一个拿到 0 → 409
        2c. auditService.append(flowId, operator, AuditAction.APPROVED, "{}");     // actor = 审批人

步骤 3  APPROVED → COMPLETED（同理三小步）
        3a. transit(FlowState.APPROVED, FlowState.COMPLETED)
        3b. updateStateIfCurrent(flowId, FlowState.APPROVED, FlowState.COMPLETED, now) → 0 → 409
        3c. auditService.append(flowId, operator, AuditAction.COMPLETED, "{}");

步骤 4  return flowRepository.findById(flowId).orElseThrow(...);
```
**跑完的结果**：状态 `COMPLETED`；加上建单那 3 行，审计共 **5 行**。

#### 方法 4：`reject(flowId, operator, reason)` —— 驳回
```
步骤 1  读单 → 空则 404；FlowState from = FlowState.of(current.state());
步骤 2  PENDING_APPROVAL → REJECTED
        2a. transit(FlowState.PENDING_APPROVAL, FlowState.REJECTED)
        2b. updateStateIfCurrent(flowId, FlowState.PENDING_APPROVAL, FlowState.REJECTED, now) → 0 → 409
        2c. auditService.append(flowId, operator, AuditAction.REJECTED, "{\"reason\":\"" + reason + "\"}");   // 理由进审计
步骤 3  return 重新读出的 FlowView;
```
**跑完的结果**：状态 `REJECTED`；加上建单 3 行，审计共 **4 行**。

**三条纪律（缺一不可）**
1. 四个公开方法都加 `@Transactional`（状态变更与审计必须同事务）；`getFlow` 只读可不加
2. 顺序**不许换**：先校验（transit）→ 再改状态（条件更新看行数）→ 再写审计
3. `actor` 只从入参来，**不要在 service 里碰 `HttpServletRequest`**（分层）

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
public enum AuditAction { FLOW_CREATED, SUBMITTED, APPROVAL_REQUESTED, APPROVED, REJECTED, COMPLETED, REVISE_REQUESTED }

public class AuditService {
    public void append(String flowId, String actor, AuditAction action, String detail);
    public List<AuditView> listByFlow(String flowId);   // 按 at 升序
}
```

**v1.1 决策（2026-10-03，Hermes 拍板）**：审计行的载体**直接复用 dsh 已定义的 `com.p2pagent.api.dto.AuditView`**（record：`auditId, flowId, actor, action, detail, traceId, at`），**不要**再定义 `AuditEntry`。
- 理由：字段完全相同，两个一模一样的 record 只会制造映射噪音；P0-02 的目标是最小闭环。
- 你负责的部分是：`AuditAction` 枚举（业务语义，引擎自己的词汇表）+ 组装 `AuditView`（生成 UUID、取 traceId、填时间）+ 调 `db/AuditRepository`。
- 留档的取舍：若将来要严格分层（内核不依赖`api.dto`），再引入 `AuditEntry` 做映射 —— 那是 P1 的事，届时**变化理由不同**（领域加字段 ≠ 契约要暴露字段）才是引入它的正当理由。

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

**自测**：一次完整流程（建单→同意）后 `select count(*) from audit_log` 应为 **5 行** —— 建单 3 条（`FLOW_CREATED` / `SUBMITTED` / `APPROVAL_REQUESTED`）+ 审批 2 条（`APPROVED` / `COMPLETED`，与 §3 的定义一致，见 §0.5 的流水表），且 5 行 `trace_id` 与本次请求那串一致。
> v1.1 修正：旧版这里写"4 行"，与 §3 的"3+2"冲突 —— 以 5 行为准。

---

## 5. 建议的写入顺序（每步都能自测，别一口气写完）

| 顺序 | 文件 | 写完立刻做的自测 |
|---|---|---|
| 1 | `FlowState` | 枚举 + `isTerminal` |
| 2 | `FlowStateMachine` | 穷举 36 组合（JUnit 或 main 里打表） |
| 3 | `AuditService` | 直接调 `append` 两次，查库有 2 行 |
| 4 | `FlowService` | 建单 → 查状态=PENDING_APPROVAL；审计 3 条；同意 → COMPLETED；审计 5 条 |

> 顺序不是随意的：**依赖方向**（状态机不依赖任何人，所以先写它）。这也是面试常问的"你怎么保证改动安全"的一个具体答案：先写无依赖的核心，再往外长。
