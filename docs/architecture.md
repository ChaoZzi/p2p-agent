# 架构 v0.2 — P2P Agent 双语言服务设计（方案 C）

> 配套 `PRD.md` v0.2。本文是**契约先行**的落地依据：先定边界与契约，再写代码。

## 1. 仓库骨架（P0 结束后形态）

```
p2p-agent/
├─ README.md                 # P0 后写：架构图 + demo 录屏 + 实测指标 + FAQ
├─ docs/
│  ├─ PRD.md                 # v0.2
│  ├─ architecture.md        # 本文
│  ├─ 评审报告-面试项目评估.md  # dsh 交叉评审原件
│  └─ task-cards/            # 任务卡（本目录）
├─ java-service/             # Spring Boot 3 / JDK21 / Maven
│  ├─ pom.xml
│  └─ src/main/java/com/p2p/
│     ├─ engine/             # 流程执行引擎（核心）
│     │  ├─ state/           # 状态机：FlowState, transitions, SQLite 持久化
│     │  ├─ idempotency/     # request_id 幂等表
│     │  ├─ compensation/    # 补偿动作注册
│     │  ├─ hitl/            # 审批节点 suspend/resume/reject/revise
│     │  ├─ audit/           # append-only 审计
│     │  └─ rules/           # 金额阈值→审批链、品类白名单
│     ├─ mock/               # mock 企业系统（同一进程内服务化 or 独立端口）
│     │  ├─ oa/              # 审批系统
│     │  ├─ finance/         # 预算校验/锁定/付款
│     │  └─ scm/             # 下单/发货/验收
│     ├─ fault/              # 故障注入开关（timeout/500/dup）
│     └─ web/                # REST API (OpenAPI)
└─ python-service/           # Python 3.11 / uv / FastAPI
   ├─ pyproject.toml
   └─ p2p/
      ├─ main.py             # FastAPI 入口 + 演示面板静态页
      ├─ harness/            # ReAct loop / 工具注册 / 上下文分层
      ├─ agents/             # 意图解析/参数抽取/澄清（flash）
      ├─ llm/                # DeepSeek client / 结构化输出 / 路由(flash↔pro) / 成本护栏
      ├─ tools/              # java-service 各端点的工具封装（契约 client）
      ├─ eval/               # 用例集 + runner
      └─ trace/              # trace 记录（JSONL，按 trace_id）
```

## 2. 契约（先行的部分）

### 2.1 透传与幂等规范
- 入口：Python 生成 `trace_id`（UUID）与 `request_id`（每步一个），HTTP 头 `X-Trace-Id` / `X-Request-Id` 透传；Java 侧所有日志/审计/响应携带。
- Java 幂等表以 `request_id` 为唯一键：重复请求返回首次结果（200 + `X-Dedup: true`）。
- 错误码语义：`4xx` 参数/状态冲突；`409 FLOW_STATE_CONFLICT`（状态机非法流转）；`422 VALIDATION`；`5xx` 可重试；业务拒绝用 `2xx + code`（如 `BUDGET_INSUFFICIENT`）。

### 2.2 关键端点（Java :8000，OpenAPI 由 springdoc 自动出）
| 端点 | 语义 |
|---|---|
| `POST /api/flows` | 创建流程实例（幂等：带 request_id） |
| `GET /api/flows/{id}` | 当前状态 + 步骤进度 |
| `POST /api/flows/{id}/approve` / `/reject` | HITL 审批（reject 带理由→revise 重提） |
| `POST /api/flows/{id}/revise` | 申请人改参重提（保留审计链） |
| `GET /api/flows/{id}/audit` | append-only 审计流水 |
| `POST /api/flows/{id}/cancel` | 取消/补偿 |
| `GET /api/flows/{id}/trace` | 回放视图（Java 侧审计 + Python 侧 trace 合并展示） |
| `GET /api/mock/faults` `PUT /api/mock/faults` | 故障注入开关（demo 用） |

Python :8001：`POST /api/chat`（自然语言→对话式执行）、`GET /api/flows/{id}`（透传）、`GET /api/eval/run`、静态演示页 `/`。

### 2.3 数据边界（不共享库）
- Java 持 `flows.db`（状态/审计/幂等，WAL）。表：`flow`、`flow_step`、`audit_log`、`idempotency`。
- Python trace 落 `trace/*.jsonl`（独立），按 `trace_id` 与审计对账。
- 状态字段由 Java 权威定义；Python 只读视图经 API，不直连库。

## 3. 状态机（Java 权威定义）

```
DRAFT(缺参澄清) → SUBMITTED → BUDGET_CHECK
   ├─ 常规低额 → AUTO_APPROVED
   └─ 超阈值/非常规 → PENDING_APPROVAL(HITL suspend)
        → APPROVED | REJECTED(→ REVISE 回 DRAFT) | TIMEOUT_ESCALATE
→ PO_CREATED → PO_SENT → SHIPPING → DELIVERED → INSPECTION_PASS
→ PENDING_PAYMENT(HITL 二次确认) → PAID → ARCHIVED
异常旁路: RETRYING(退避≤3) / SUSPENDED(升级人工) / COMPENSATED / CANCELLED
```
表驱动：`FlowState` enum + `Map<FlowState, Set<FlowState>>` 合法迁移表；非法迁移返回 `409`。进程重启恢复：启动扫描 `PENDING_*/RETRYING/SUSPENDED` 挂起单。

## 4. 故障注入（P1 演示素材，P0 留开关）
mock 侧按 `(system, op, mode)` 配置：`delay>超时` / `500` / `duplicate`。P0 先做 timeout + duplicate；P1 加 500+补偿演示。

## 5. P0 冒烟（第一张卡，1–2 天）验收
- [ ] Maven 装好，`java-service` 空 Spring Boot 工程能起
- [ ] mock-oa 最小端点 + 故障开关
- [ ] OpenAPI 契约文件落地（springdoc 导出 json 入库 docs/contracts/）
- [ ] Python client 调通 mock-oa（含 X-Trace-Id 透传断言）
- [ ] 失败标准：任一步卡壳 > 2 天 → 走 PRD §4 降级路径

## 6. 面试叙事一页纸（5 分钟故事线）
一句话 → 谁决定步骤（规则主干 + LLM 分支）→ 状态机为何自研（可审计/可回滚）→ HITL 边界（钱必须人批）→ 双语言为何（JD 同款拆法）→ 故障怎么活（重试/补偿/升级）→ 怎么证明有效（Eval 数字）→ 审计回放演示
