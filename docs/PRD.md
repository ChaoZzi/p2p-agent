# PRD v0.2 — P2P Agent：跨系统「采购到付款」流程执行型数字员工（Java + Python 双语言版）

| 项 | 内容 |
|---|---|
| 版本 | **v0.2**（2026-09-04，吸收 DeepSeek harness 交叉评审） |
| 变更 | v0.1 纯 Python → 方案 C 双语言；里程碑 P0/P1/P2 重排；叙事对齐招聘语言与应届身份 |
| 评审依据 | `docs/评审报告-面试项目评估.md`（dsh 产出，含 8+ 真实 JD 证据链接） |
| 目录 | `D:\Projects\interview-project\p2p-agent` |

---

## 1. 定位（应届身份修正版）

> 以「采购到付款（P2P）」业务为**载体**，证明 Agent 系统工程能力：**确定性流程交给 Java 工程（状态机/幂等/审计/HITL），不确定性模型决策交给 Python AI 生态（ReAct/工具注册/路由/评测）**——双服务、契约先行、trace_id 贯穿。

面向岗位：Agent 应用开发（业务向，本科校招/应届入口，如卓望数码类岗位）+ 可延伸 Java 系 Agent 岗（智联 Agent 平台开发 JD 的模块要求与本项目逐词对应）。

## 2. 目标岗位关键词 → 本项目落点（README/简历同文）

| JD 词 | 本项目实体 |
|---|---|
| 工作流编排 / 状态机控制 | 流程执行引擎（Java）：步骤状态机、SQLite 持久化 |
| 失败恢复 / 重试 | 指数退避重试、补偿（预算锁定失败→解锁） |
| 审批中断 / HITL | PENDING_APPROVAL / PENDING_PAYMENT 节点 suspend/resume |
| 运行回放 / 链路追踪 | trace_id 全链路透传 + 审计流水 + 回放 API |
| 工具调用 / 幂等预占 / 权限校验 | Python 工具注册（类 MCP schema）+ request_id 幂等 + 权限分级 |
| 模型路由 / token 成本护栏 | flash（意图/抽取）→ pro（规则外决策），单流程预算上限 |
| 评测闭环 / 观测 | 15–20 条端到端用例 + 自动化率/通过率/成本指标 |

## 3. 范围

**In scope（按 P0/P1/P2 分级，见 §5）**
- Java 服务（方案 C 的"企业侧"）：流程执行引擎（状态机/幂等/补偿/超时/HITL/审计/权限）+ Mock 企业系统 ×3（oa/finance/scm，带故障注入）
- Python 服务（主干）：自研 Agent Harness（ReAct loop/工具注册/上下文分层）+ 意图解析/澄清 + 模型路由/成本护栏 + 演示面板 + Eval/Trace
- 通信契约：HTTP + OpenAPI；request_id/trace_id 规范；错误码语义

**Out of scope（砍单清单，永远可牺牲）**
真实 ERP/OA 集成、SSO 账号体系、审批链可视化配置 UI、消息推送（微信/邮件）、supervisor 多 Agent（P2）、MCP Server 化（P2）、Docker 化（P2）、LangGraph 对照 demo（P2，仅认知不交付）

## 4. 双语言架构（方案 C）

```
┌─────────────────────────────────────────────────────┐
│ Python 服务 :8001（主干）                            │
│  演示面板(FastAPI) · Agent Harness(ReAct loop)       │
│  工具注册(类MCP schema) · 意图/参数抽取(flash)        │
│  澄清追问 · 规则外决策(pro) · 成本护栏 · Eval/Trace   │
└──────────────┬──────────────────────────────────────┘
               │ HTTP + OpenAPI 契约
               │ request_id / trace_id 全链路透传
┌──────────────▼──────────────────────────────────────┐
│ Java 服务 :8000（企业侧 · Spring Boot 3 / JDK21）     │
│  流程执行引擎: 状态机(SQLite) · 幂等 · 补偿 · 超时重试 │
│  HITL 审批(suspend/resume/驳回修订) · append-only审计  │
│  权限分级 · 错误码语义                                │
│  Mock 企业系统 ×3: mock-oa / mock-finance / mock-scm  │
│  (故障注入开关: 超时/500/重复请求)                    │
└─────────────────────────────────────────────────────┘
数据边界: Java 持 flows/audit(SQLite, WAL)；Python trace 落独立 JSONL/小库，按 trace_id 关联。不共享库文件。
```

职责与面试话术：**"确定性的资金流程（状态机/幂等/审计）交给 Java 后端工程；不确定性的模型决策（意图/澄清/异常建议）交给 Python AI 生态"** ——与智联/阿里云 JD 把 Agent 运行时拆成"确定性工程 + AI 工程"的组织方式一致。

**降级路径（B 计划）**：双语言联调卡壳时，先 Python 直连 Java Mock 跑通主链路（Mock 是 Java 侧最轻入口），引擎状态机保持在 Java（哪怕第一步只做 HITL + 审计），每一步都有可演示产物。

## 5. 里程碑（2–3 周 × 5h/天收口；P0 ≤ 第 2 周末）

| 优先级 | 内容 | 验收（可录屏） |
|---|---|---|
| **P0（约 45–50h，硬线 ≤ W2 末）** | ① 契约先行冒烟（Java Mock-oa + Python client 调通 + trace_id 透传，1–2 天）→ ② Java：状态机 + 审计 + HITL 一节点 + mock-finance 预算校验 + mock-scm 下单/验收/付款 → ③ Python：ReAct harness + 工具注册 + 意图/抽取 + 一次澄清 → ④ 主链路端到端 + 最简页面 | **录屏 3 分钟：一句话发起→状态推进→审批→完成→审计回放** |
| P1（W3，做不完进 P2 清单） | 幂等/补偿/超时重试（故障注入演示）、驳回修订重提、权限分级、Eval 15 条跑分、README 三指标图 | README：通过率/自动化率/成本对比实测数字 |
| P2（加分，可全砍） | supervisor 多 Agent、一个工具 MCP Server 化、Docker 一键起、LangGraph 对照 demo | 简历写"加分项"；没做不写 |

**框架认知卡（2h，防"话术不实"）**：通读 LangGraph StateGraph/节点/持久化文档 + 跑一个 hello demo；查 Flowable 定位。让"对比过主流方案后自研"成为事实。

## 6. 评测指标（P1 实测后写，没跑的数字不写）

端到端完成率 · 无需人工干预率 · 平均步数/延迟 · 单流程 token 成本（路由前后对比）· 故障注入自动恢复率 · 审计回放率 100%

## 7. 技术栈与关键取舍

| 决策 | 选择 | 理由（面试可讲） |
|---|---|---|
| Python 主干 | 3.11 + FastAPI + uv + Pydantic | Agent 生态；结构化输出校验 |
| Java 企业侧 | Spring Boot 3 + JDK 21 + sqlite-jdbc（WAL） | 状态机/事务/审计是 Java 主场 |
| 不用 Activiti/Flowable/Spring AI | 自研轻量状态机 | 主干可审计可回滚可测试；Spring AI 是社招 JD 词，应届不引真依赖 |
| 不用 LangGraph/CrewAI | 自研薄 harness（几百行） | "我写过 loop"；对照实验留认知卡 |
| 存储 | Java: SQLite（抽象可换 PG）；Python: JSONL trace | 见 §4 数据边界 |
| 模型 | DeepSeek v4-flash / v4-pro 路由 | 成本杠杆；key 复用 Hermes .env（不入库，.env + .gitignore） |

## 8. FAQ（README/面试防御）

- **为什么不用 RAG？** P2P 是动作执行场景，决策信息在结构化单据里，不需要非结构化知识检索；扩展点=供应商知识库查询工具。
- **为什么不用现成工作流引擎/框架？** 花钱动作不交给黑盒；自研换可审计/可回滚/可测试；已做主流方案认知对照。
- **为什么 Java + Python 两门语言？** 与 JD 的运行时拆分类似：确定性工程 vs AI 工程，各用最合适的语言；跨语言可观测性（trace_id）本身就是加分叙事。
- **多 Agent？** P2 加分项；当前单 Agent + 确定性引擎已覆盖流程执行场景，多 Agent 的上下文/成本开销在此不划算。

## 9. 面试三维表达（v0.2 版）

- **架构**：Java 流程引擎（状态机/幂等/补偿/HITL/审计/权限）+ Python 自研 Harness（loop/类 MCP 工具注册/模型路由）+ OpenAPI 契约 + trace_id 贯穿
- **业务**：自然语言发起 P2P → 预算校验 → 审批 → 下单 → 验收 → 付款，含澄清/驳回修订/预算不足/故障恢复
- **结果**：P1 实测：通过率 X% / 自动化率 X% / 成本降 X% / 恢复率 X%（未测不写）
