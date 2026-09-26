# P0-02 §0 执行简报（给 dsh）— 存储双 profile 改造

> 配套：`docs/task-cards/P0-02-min-loop.md §0`（卡）、`docs/architecture.md §2.3`（设计）、`docs/PRD.md v0.3 §7/§8`（决策）
> 口径：**有冲突以本简报为准**（它写得更细）。设计责任：Hermes。执行：dsh。
> 这是 P0-02 的**第一步**，独立 commit；做完**停下等 Hermes 审核**，不要连着做 P0-02 主体。

## 1. 目标（一句话）

让**同一套 DAO/SQL** 在 `sqlite`（默认，零依赖）与 `postgres`（本机 PG 16）两个 profile 下**行为完全一致**，为 P0-02 主体的 4 张业务表铺好地基。

**为什么现在做**：P0-01 交付时只有 SQLite。等主体把 `flow`/`flow_step`/`audit_log` 建完再改，就要重做一批 DAO + 重跑一批证据；现在改只动 6 个文件、零返工。

## 2. 硬纪律（同 P0-01 简报，重申）

- 只写 §3 列出的文件；**不要动** Python 侧、`docs/`（除新增 `docs/task-cards/P0-02-讲解.md`）、`.idea/`、`.gitignore`（Hermes 已补好 `application-*-local.properties`）。
- 交付 = 代码 + **`docs/task-cards/P0-02-讲解.md`**（逐文件"做什么/为什么/改了会怎样"）+ **真实自测证据**（命令与输出原样贴，不许改写、不许伪造）。
- commit 粒度：一个可演示增量一个 commit，`feat(java):` / `refactor(java):` / `docs:` 前缀；只 commit 自己负责的文件。
- **密码卫生**：仓库里只允许出现占位符（`${PG_PASSWORD}` 等）；任何真实密码不得进文件、不得进 commit（含历史）。

## 3. 改动清单（6 个文件）

| 文件 | 要做什么 |
|---|---|
| `java-service/pom.xml` | 加 `org.postgresql:postgresql`（**不写版本**，交给 Boot BOM），scope `runtime` |
| `src/main/resources/application.properties` | 只留**共用项**（端口、日志 pattern、`spring.sql.init.mode=always`、`spring.profiles.default=sqlite`）；把 `spring.sql.init.platform` 写成 `${spring.profiles.active:sqlite}` 或按 profile 分别声明（二选一，讲清理由） |
| `src/main/resources/application-sqlite.properties`（新） | SQLite URL / 驱动 / Hikari `maximum-pool-size=1` / `busy_timeout=5000` |
| `src/main/resources/application-postgres.properties`（新） | PG URL `${PG_URL:jdbc:postgresql://127.0.0.1:5432/p2p_agent}` / 驱动 `org.postgresql.Driver` / 用户 `${PG_USER:p2p_agent}` / 密码 `${PG_PASSWORD}` / Hikari `maximum-pool-size=10`；**不要**在文件里放真实密码 |
| `src/main/resources/schema.sql` → 拆成 `schema-sqlite.sql` + `schema-postgres.sql` | 两版 DDL 都**幂等**（`CREATE TABLE IF NOT EXISTS`）；字段名/类型语义**必须一致** |
| `service/ApprovalService.java` | 两条 SQL 改为**两边通用**写法：`INSERT INTO idempotency(...) VALUES (?,?,?,?) ON CONFLICT(request_id) DO NOTHING`（去掉 SQLite 专有的 `INSERT OR IGNORE`）；`SELECT` 不变；改完**注释说明为什么这个子集是通用的** |

**已定设计（照做，不要自创）**
- `created_at` 两库都用 `TEXT` 存 **ISO-8601 字符串**（`Instant.toString()`）。理由：与 HTTP 契约里 `created_at` 是字符串一致、跨库/跨语言零类型映射、Python 侧不用管时区。**面试会追问"为什么不用 timestamptz"** → 答案写进讲解：P2 若要做时间范围查询/索引，再加一列 `created_at_ts timestamptz` 示范，而不是把契约改成时间类型。
- PG 侧 DDL 类型映射：`TEXT` → `TEXT`；`PRIMARY KEY` → `PRIMARY KEY`（`request_id TEXT PRIMARY KEY`）。**别用** `serial`/`identity`（P0-02 主键是业务 id 字符串，不是自增）。
- **审计 append-only 权限不在本步做**（`audit_log` 表还没建）→ 留给 P0-02 主体建表时一并处理，本步只需要在讲解里写清计划。

## 4. 验收（全部要实测证据）

- [ ] **回归**：默认 profile（不传任何参数）启动 → P0-01 的 7 条验收与 21 项断言**原样全过**（trace 回显 / 幂等双调 / 4 类 422 / 故障延迟 ≥3s / 非法 trace 兜底）
- [ ] `--spring.profiles.active=postgres` 启动 → **同样的断言在 PG 16 上全过**；且 PG 库里同 `request_id` 只有 1 行（贴 `select` 结果）
- [ ] **5 并发同 request_id 在 PG 上也只建 1 行**（1×false + 4×true，`approval_id` 全同）→ 这是 PG 相对 SQLite 的真实能力，必须实测
- [ ] **platform 生效证明**：两个 profile 分别启动后，各自库里的 `idempotency` 表由对应 `schema-*.sql` 建出（贴两边的 DDL 或 `\d idempotency` 输出对比）
- [ ] **凭据卫生**：`git grep -i "password"` 只有占位符；`git check-ignore -v java-service/src/main/resources/application-postgres-local.properties` 有输出
- [ ] 讲解覆盖：为什么两份 schema（而不是一份带 dialect 分支）、通用 SQL 子集到哪为止、再复杂怎么办（答：P1 若 SQL 分化加剧改用 Flyway 按 dialect 管迁移）、两个 profile 的连接池差异理由

## 5. PG 侧准备（用户做，你不必碰凭据）

用户会在本机执行一次 `scripts/pg-init.sql`（建角色 `p2p_agent` + 库 `p2p_agent`），并把密码放进**本地未提交**的 `java-service/src/main/resources/application-postgres-local.properties`（`spring.datasource.password=***`）。

**你启动 PG profile 的方式**（这条可用于自测）：
```bat
java-service> set PG_PASSWORD=<由用户提供/已在本地文件里>
java-service> ..\scripts\run-java.cmd -Dspring-boot.run.profiles=postgres
```
或直接在 IDEA / maven 里加 `-Dspring-boot.run.profiles=postgres`。**如果 PG 连不上**：先确认服务在跑（`netstat` 看 5432）→ 确认角色/库已建 → 仍然不通就**停下报告**，不要改成 H2 之类的替代品糊过去。

## 6. 失败预案

- PG 侧确实不可用（服务/权限问题）→ 允许**只交付 sqlite 侧的结构改造**（profile 拆分 + schema 拆分 + 通用 SQL），PG 侧留 `TODO` 并在讲解里如实说明"未验证"，**不要伪造 PG 证据**。Hermes 审核时会据此决定是否阻塞主体开工。
- 通用 SQL 子集撞墙（比如某条语句两边真的写法不同）→ 如实上报，附两边各自的写法与差别，由 Hermes 决定是"局部用 profile 专属 SQL"还是"改设计"。
