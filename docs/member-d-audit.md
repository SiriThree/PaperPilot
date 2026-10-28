# 成员 D 任务 6：关键安全审计与失败关闭

2026-10-28。SecurityAdapter 已复用 DataPort.append_audit 建立执行前后门槛，并在 A 固定版本 `0b2953c218668af71a4bf869e5c6d238cc0d588d` 的真实 PostgreSQL 16 迁移上验证提交与回滚。默认 build_security(data) 不配置 SQL 执行器，返回 allowed/not_executed；后续任务 4/5/7 已接入[受限执行器](member-d-executor.md)，正式部署仍未验收。

## 事件与门槛

| 路径 | D 的关键事件 | 行为 |
|---|---|---|
| 策略通过且配置执行依赖 | security.query.prepared / allowed / pending | append_audit 返回成功后，重新检查成员关系，再调用执行依赖。失败或确认未知均不执行。 |
| 执行成功且结果符合现有 Schema | security.query.finished / allowed / executed | 再次检查成员关系，确认后审计追加成功，再返回结果。审计失败不返回列或行，不能声称 SQL 没有运行。 |
| 执行依赖异常、超时或结果格式错误 | security.query.finished / undetermined / failed | 返回脱敏的 QUERY_EXECUTION_FAILED 或 QUERY_TIMEOUT，无结果；审计失败改为 AUDIT_UNAVAILABLE。 |
| 任务取消 | security.query.finished / undetermined / cancelled | 等执行依赖的取消清理退出后追加事件，继续传播 CancelledError。取消事件写入失败仍保留取消，不返回结果。 |
| 不支持问题或策略拒绝 | security.query.finished / rejected / not_executed | 不调用执行依赖，确认审计后返回拒绝。 |
| 默认执行关闭 | security.query.finished / allowed / not_executed | 确认审计后返回 QUERY_EXECUTION_DISABLED。 |
| 生成/策略内部故障或已接受请求期间撤权 | security.query.finished / undetermined / failed | 记录安全状态后抛出脱敏网关/权限错误，不返回结果。 |

初始 Context、成员关系及输入 Schema 校验仍先于候选处理。初始无权限或格式无效的调用不会以其声明项目追加 D 查询事件；B 的入口审计继续按既有逻辑处理。本项没有扩展 authorize 的审计范围。

所有事件明确 mock=false，使用可信 actor_id/project_id/request_id、策略标识 postgres-statistics-v2、带时区时间戳和非负 duration_ms。每个事件独立生成 event_id；同一 request_id 下的不同阶段和不同调用不会被误去重。仅使用现有 AuditEvent 字段，不保存自然语言问题、原始候选 SQL、查询计划、结果内容、异常正文或凭据。

append_audit 的成功返回是本地持久化确认，既不等待也不依赖异步 outbox。B 的 secure_query.requested/finished 继续作为业务日志，不能满足 D 的门槛。任何审计异常统一转为 HTTP 503 AUDIT_UNAVAILABLE，文案为“关键安全审计未确认，未返回查询结果”。失败不自动重试 SQL，也不新建替代事件；已提交事件可用原 event_id 和完整原内容按 A 的幂等语义对账/重放。

## 内部依赖与边界

为验证门槛，D 构造器新增仅供可信组合代码配置的 query_executor 关键字参数：异步 `(Context, QueryPlan) -> (columns, rows)`。调用发生在 AST 校验和执行前审计之后，消费重建后的参数化计划。默认值为 None；没有新增 HTTP 参数、SecurityPort 方法、环境变量或启动开关，没有调用 DataPort.controlled_statistics 绕过执行链路。

该依赖负责数据库取消与连接清理；门槛只在 await 退出后处理完成事件，不把普通返回包装成资源限制证据。PostgresQueryExecutor 现已实现独立只读连接、权限检查、3 秒/100 行/256 KiB、驱动映射与取消清理，并通过真实 SecurityHarness/攻防验证，见[执行器说明](member-d-executor.md)。同步 DataPort 审计调用的耗时仍由 A 的连接/事务行为决定，没有新增异步审计投递器或审计超时保证。完成审计后还复查成员关系，finished/executed 描述 SQL 完成，不保证客户端已收到结果。

只读登录已在隔离测试中禁止访问 audit_events/outbox；审计调用使用注入的 DataPort。测试中的 A DataPort 使用独立测试管理员连接，不证明正式审计写身份已最小授权或防篡改。正式角色生命周期、审计写身份和业务库部署仍需 A/D 联合落实。

## 验证与复现

- test_security_audit.py 的 25 项测试使用可控依赖验证前后顺序、故障/确认丢失、脱敏、撤权、取消、格式错误、event_id 与 HTTP 门槛，含完成审计期间撤权。它不是数据库资源清理测试；实际执行器的前后门槛另在 test_security_executor_postgres.py 验证。
- test_security_postgres_contract.py 在既有 22 项基础上新增 5 项：独立连接观察执行前提交、受限登录实际读取、完成事件持久化、同事件重放/冲突，以及前后审计 INSERT 后事务回滚或提交后确认丢失。确认丢失由真实提交后抛错模拟，未声称注入了网络断线。
- Python 3.12.10 全量后端 **430 passed / 37 skipped / 0 failed**，含 27 项实际 PostgreSQL 测试；跳过仍为 31 项历史 SQLite 实验和 6 项默认 Mock 数据层真实库门槛，未计作通过。报告为 `.runtime/d-step3-combined-results.xml`。
- 测试沿用 A 的固定参考和自动清理的一次性容器，不触及业务库；显式开启后缺环境必须报错。复现前置见 [A/D 约定](ad-security-contract-proposal.md)。

```powershell
$env:PAPERPILOT_SECURITY_POSTGRES_TESTS='1'
$env:PAPERPILOT_SECURITY_TEST_A_SOURCE=(Resolve-Path '.runtime/member-a-step2-reference').Path
.\.runtime\venv312\Scripts\python.exe -m pytest -q -rs --tb=short --junitxml=.runtime/d-step3-combined-results.xml
```
