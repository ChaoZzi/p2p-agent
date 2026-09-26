# P2P Agent

跨系统「采购到付款」流程执行型数字员工：自然语言发起采购，Agent 跨 OA/财务/采购完成预算校验 → 审批 → 下单 → 验收 → 付款，高风险动作人工确认，全程可审计、可回放。Java 侧负责确定性流程骨架，Python 侧负责 Agent 编排与模型决策。

> 当前进度：**P0-01 契约先行冒烟**（mock-oa 服务 + Python 客户端 + trace 透传/幂等验证）。详细验收证据见 `docs/task-cards/P0-01-讲解.md`。

## 启动

**方式一（推荐：一键脚本，在任意目录都能跑）**

```bat
scripts\run-java.cmd        :: Java mock-oa，起在 :8000（默认 sqlite profile）
scripts\run-python.cmd      :: 另开一个窗口，跑 Python 端自测（trace 透传 + 幂等）
```

**方式二（手动；注意先切到项目根目录，否则 `-f` 的相对路径会找不到）**

```bat
cd /d D:\Projects\interview-project\p2p-agent
D:\software\maven-dist\apache-maven-3.9.9\bin\mvn.cmd -f java-service/pom.xml spring-boot:run

:: 另一个终端窗口
cd python-service && uv run python -m tools.oa_client
```

> **切 PostgreSQL（可选）**：`scripts\run-java.cmd -Dspring-boot.run.profiles=postgres`
> 前置：先用 psql 跑 `scripts\pg-init.sql` 建角色/库，并把密码写进**本地未提交**的
> `java-service\src\main\resources\application-postgres-local.properties`（已被 .gitignore 覆盖）。
> 详见 `docs/architecture.md` §2.3。
