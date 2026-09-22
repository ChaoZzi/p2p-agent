# P2P Agent

跨系统「采购到付款」流程执行型数字员工：自然语言发起采购，Agent 跨 OA/财务/采购完成预算校验 → 审批 → 下单 → 验收 → 付款，高风险动作人工确认，全程可审计、可回放。Java 侧负责确定性流程骨架，Python 侧负责 Agent 编排与模型决策。

> 当前进度：**P0-01 契约先行冒烟**（mock-oa 服务 + Python 客户端 + trace 透传/幂等验证）。详细验收证据见 `docs/task-cards/P0-01-讲解.md`。

## 启动

```bash
# Java 侧 mock-oa（:8000）—— Maven 用全路径，不要用 git-bash 里的 mvn
D:\software\maven-dist\apache-maven-3.9.9\bin\mvn.cmd -f java-service/pom.xml spring-boot:run

# Python 侧 oa_client 自测
cd python-service && uv run python -m tools.oa_client
```
