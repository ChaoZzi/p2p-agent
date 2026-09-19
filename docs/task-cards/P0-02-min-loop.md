# P0-02 任务卡 — 最小闭环系统（先让你看懂整个架构）

> 前置：P0-01（双服务冒烟）已完成，Java :8000 与 Python :8001 已能互调、trace_id 已透传。
> 目标：**最简但每个架构层都真实存在的纵向切片**。做完这张卡，你能指着每一层说"它干什么、为什么在这"。
> 本卡刻意砍掉：预算校验、下单/付款、补偿、驳回修订、评测——都留到 P0-03+，防止最小系统变重。

## 1. 业务故事（演示脚本）
> 页面输入："我想申请采购 1 台 MacBook Pro，给新来的前端用"
→ Agent 抽取意图=采购申请 + 参数（item=MacBook Pro, qty=1, reason=给新来的前端用），缺 costCenter → **反问一次**
→ 你补："成本中心填 DEV-01"
→ Agent 调 Java 创建流程单 → 状态 DRAFT→SUBMITTED→PENDING_APPROVAL
→ 页面出现"待审批"，你在页面点【同意】
→ 状态 → APPROVED→COMPLETED，Agent 汇报"已办结，审计可查"
→ 页面侧栏显示审计流水（谁/何时/哪一步/调了谁）

## 2. 状态机（本卡范围，Java 权威定义）
```
DRAFT → SUBMITTED → PENDING_APPROVAL → APPROVED → COMPLETED
                            └→ REJECTED（本卡只落状态，驳回修订流程 P0-04）
```
非法迁移返回 `409 FLOW_STATE_CONFLICT`。

## 3. 契约（Java :8000，本卡新增）
| 端点 | 说明 |
|---|---|
| `POST /api/flows` | body: {request_id, item, qty, reason, cost_center}；幂等：同 request_id 返回原单 |
| `GET /api/flows/{id}` | 状态 + 参数 + 时间线摘要 |
| `POST /api/flows/{id}/approve` | 审批通过（header X-Operator 表示审批人） |
| `POST /api/flows/{id}/reject` | 审批驳回 |
| `GET /api/flows/{id}/audit` | append-only 审计流水 |

## 4. 文件与代码归属

### Java 服务（java-service，包 com.p2p）
| 文件 | 归属 | 说明 / 规格 |
|---|---|---|
| `pom.xml`、`application.yml`、启动类 | [dsh写+讲] | Spring Boot 3 + sqlite-jdbc + springdoc；含 CORS（允许 :8001） |
| `schema.sql`（flows/audit_log/idempotency 表） | [dsh写+讲] | 字段最小化；audit_log 只 insert |
| `engine/FlowState.java` | **[你敲]** | enum：DRAFT/SUBMITTED/PENDING_APPROVAL/APPROVED/REJECTED/COMPLETED |
| `engine/FlowStateMachine.java` | **[你敲]** | 合法迁移表 Map<FlowState,Set<FlowState>> + `canTransit(from,to)`；这是全项目最核心的 30 行之一，面试必问 |
| `engine/FlowService.java` | **[你敲]** | 建单/提交/审批/驳回 的业务编排；每步：校验状态→迁移→写审计→落库（同事务） |
| `engine/AuditService.java` | **[你敲]** | append-only：记录 actor/action/flow_id/时间/摘要 |
| `engine/IdempotencyService.java` | [dsh写+讲] | request_id 唯一约束，重复请求返回原结果 |
| `db/FlowRepository.java`（JdbcTemplate） | [dsh写] | CRUD + 迁移更新 |
| `web/FlowController.java` | [dsh写] | REST 映射、错误码语义（409/422） |
| `mock/MockOaController.java` | [dsh写+讲] | 本卡不需要？→ 保留 P0-01 的 /healthz 即可，审批逻辑直接在引擎内做（mock-oa 留给 P0-03） |

### Python 服务（python-service）
| 文件 | 归属 | 说明 / 规格 |
|---|---|---|
| `pyproject.toml`、`main.py`、静态页面 | [dsh写+讲] | FastAPI :8001；`/` 返回演示页（聊天窗 + 状态侧栏 + 审计列表）；`POST /api/chat` |
| `llm/deepseek.py` | [dsh写] | 统一调用封装（flash 模型、JSON mode、超时/重试一次） |
| `harness/tool_registry.py` | **[你敲]** | 工具注册表：name/description/参数 schema/executor；含校验 |
| `harness/agent.py` | **[你敲]** | **最小 ReAct 决策循环**：用户话→(调 LLM 决策：抽取参数/反问/调工具)→执行→拼接结果→最多 3 轮→回复。规格：单轮内决策用 JSON `{action, args}`，工具执行结果回填后决定继续或结束 |
| `tools/flow_client.py` | [dsh写] | Java 契约 client（POST flows / approve / audit…），自动带 X-Trace-Id |
| `agents/intent.py` | **[你敲]** | 意图=采购申请 的参数抽取（item/qty/reason/cost_center），缺 cost_center 时产出"反问问题"；Pydantic 校验 + 一次纠错重试 |
| `trace/tracer.py` | [dsh写] | 每步记 JSONL（trace_id/时间/动作/结果） |
| `web/chat_api.py` | [dsh写+讲] | /api/chat：串起 agent+tools，返回回复/当前状态/审计 |

## 5. 验收（全部满足才 commit）
- [ ] 演示脚本 5 步完整可跑（页面操作，无命令行黑盒）
- [ ] 重复 POST /api/flows（同 request_id）返回原单号（幂等生效）
- [ ] 对 COMPLETED 单调 approve 返回 409
- [ ] 审计流水至少含：创建/提交/审批/完成 四类记录，且为 append-only
- [ ] 你在终端 curl 或页面里能指出每个状态变化的代码位置（理解检查）
- [ ] dsh 交付《讲解.md》，逐文件讲清

## 6. Commit 计划（本卡 ≥2 个 commit）
1. `feat(java): 流程引擎最小闭环（状态机/审计/幂等/HITL 端点）` —— dsh 提交非 [你敲] 文件后，**你敲完 [你敲] 文件再合**：顺序 = dsh 先交付骨架 → 你照规格敲核心 → 联调 → 你 commit 核心、dsh 提交其余（或你统一 commit 并注明，按协作规范 §3）
2. `feat(python): 最小 agent 闭环（harness/意图/聊天页）`

## 7. 讲解重点（dsh 必须在讲解.md 覆盖）
- Java 侧为什么"先校验状态再迁移再审计再落库"（事务顺序）
- Python 侧 agent 循环为什么是"决策 JSON → 执行 → 回填 → 再决策"而不是把全部步骤一次生成
- trace_id 在两次服务调用间怎么串起来
