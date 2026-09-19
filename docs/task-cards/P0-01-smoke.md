# P0 任务卡 01 — 契约先行冒烟（双语言链路验证）

> 目标：**先证掉"Java + Python 双语言不卡壳"这个最大假设**。1–2 天（约 5–10h）。执行：dsh harness（Java/Python 编码）→ 用户 review 合入。设计责任：Hermes（本卡契约内容为准）。

## 验收（全部满足才算过）
- [ ] Maven 已装（`mvn -version` 可跑），`java-service` 空 Spring Boot 3 工程能 `mvn spring-boot:run` 起在 :8000
- [ ] mock-oa 实现最小端点：`POST /api/mock/oa/approvals`（模拟审批创建，带随机延迟开关）+ `GET /healthz`
- [ ] 故障开关骨架：`PUT /api/mock/faults` 可注入 `timeout`（sleep 5s）
- [ ] OpenAPI 契约导出为 `docs/contracts/java-service-openapi.json`（springdoc）
- [ ] Python 侧 `tools/oa_client.py` 调通 mock-oa；**断言 `X-Trace-Id` 从 Python 生成并在 Java 响应/日志中透传**
- [ ] 根 README 占位已建；`.gitignore`（含 `.env`、`*.db`、`trace/*.jsonl`）
- [ ] 全程 request_id 幂等语义在 mock-oa 一个写端点落地（重复请求返回原结果）

## 前置
- 装 Maven：`scoop install maven`（D:\scoop 已存在；装完 `mvn -version` 验证）
- DeepSeek key：本轮不涉及（冒烟无 LLM 调用）；M2 起复用 `~/AppData/Local/hermes/.env` 的 DEEPSEEK_API_KEY，写入 python-service/.env（gitignore）

## 分工与顺序
1. （Hermes）确认契约字段 → 2. （dsh）Java 骨架 + mock-oa + springdoc → 3. （dsh）Python client + 透传断言 → 4. （用户）review + 合入 + 录屏留档

## 失败预案
任一步卡壳 > 2 天 → 按 PRD §4 降级：Java 侧砍到只剩 mock-oa + 审计最小实现，主链路先 Python 内直连。
