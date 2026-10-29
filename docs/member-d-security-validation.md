# 成员 D 任务 8/9：真实攻防与组合验证

2026-10-28，Windows / Python 3.12.10 / PostgreSQL 16 / psycopg 3.3.6。测试使用自动清理的独立 Docker 容器，固定 A `0b2953c218668af71a4bf869e5c6d238cc0d588d`、C `1f430936acf23c598a91ce794d70ca8c536d39e4`。没有合并 A/C 分支、修改远程 PR 或连接业务库。

## 攻防与资源验证

test_security_executor_postgres.py 实际配置 PostgresQueryExecutor 和 A DataPort，不以 MockSecurity 或 DataPort.controlled_statistics 代替执行器。DataPort 的统计方法只用于结果对照。

| 用例组 | 证据 |
|---|---|
| 44 项攻击 | 写操作、DDL、多语句、注释、任意表/列、JOIN/CTE/子查询/UNION、函数、锁、项目条件替换等，经过实际配置的安全链路拒绝；无执行前事件/结果内容泄露。不是把攻击 SQL 发给数据库执行后再过滤。 |
| 21 项正常 | 三种正式统计各七种合法 AST 变体，交叉使用两个项目和 owner/member；实际受限连接的结果与 A 对照，含空年份、多主题、已移除论文、证据存在性。 |
| 直接绕过 | 44 项直接伪造 QueryPlan，以及项目参数篡改，均在执行器边界拒绝。独立视图契约另覆盖直接底表访问、写入、身份切换和 GUC 伪造。 |
| 身份/权限变化 | 管理员登录、危险角色标志、NOINHERIT 角色成员关系、表/列/序列/其他 schema 权限、审计/outbox/绑定表访问、SECURITY DEFINER 执行等均失败关闭。 |
| 资源限制 | 实际只读事务与固定设置；锁超时；放宽客户端计时但保留服务端 3 秒限制的慢视图测试；105 个额外主题最多返回 100 行；多字节主题触发 256 KiB 限制且无部分结果。 |
| 取消/故障/清理 | 实际表锁下取消及重复取消、后台连接终止、下一请求新连接、并发项目隔离；返回/异常后观察 pg_stat_activity 确认没有残留读取会话。 |
| 实际执行器审计门槛 | 执行前/后审计失败与提交后确认丢失模拟，不执行或不返回行；另有 A 真实事务 INSERT 后回滚、事件持久化与幂等重放测试。 |

test_security_executor.py 另验证驱动类型转换、非有限数值拒绝，以及包含完整元数据的响应在 256 KiB 边界内外的行为。既有审计/授权/AST 测试全部保留。

## A+D，再 A+C+D

test_security_team_integration.py 依次完成两个 HTTP 工作流：A 正式 PostgresData + D 实际执行器 + C=Mock；随后 A + C RealAI/bootstrap + D。覆盖登录、上传/处理、检索、证据前后统计、问答、草稿、报告保存、三个安全统计、关键审计、越权拒绝和 A 连接池生命周期。C 模型传输故障时安全统计仍独立可用。

C 的 PDF reader 和 parser 子进程、检索、ModelClient 的 HTTP 传输/结构校验、引用处理都运行真实代码；模型端为本地回环 HTTP 可控替身，未调用外部模型或下载权重，不代表实际模型效果验收。A 当前 ProcessResult 路径的 index_state 仍为 mock_only；本测试没有补建持久化向量索引。

```powershell
# 首次准备固定参考；已有工作树时只检查 HEAD，不覆盖目录。
git worktree add --detach .runtime/member-c-integration-reference 1f430936acf23c598a91ce794d70ca8c536d39e4
.\.runtime\venv312\Scripts\python.exe -m pip install -r requirements.lock -r requirements-security-test.lock
$env:PAPERPILOT_SECURITY_POSTGRES_TESTS='1'
$env:PAPERPILOT_SECURITY_TEAM_TESTS='1'
$env:PAPERPILOT_SECURITY_TEST_A_SOURCE=(Resolve-Path '.runtime/member-a-step2-reference').Path
$env:PAPERPILOT_SECURITY_TEST_C_SOURCE=(Resolve-Path '.runtime/member-c-integration-reference').Path
.\.runtime\venv312\Scripts\python.exe -m pytest -q -rs --tb=short --junitxml=.runtime/d-remaining-combined-results.xml
# 默认共享契约仍是 Mock；真实 D provider 单独选择，不与上述数字相加冒充独立用例。
.\.runtime\venv312\Scripts\python.exe -m pytest backend/tests/security_contracts --security-contract-provider security_contracts.postgres_provider:build_harness -q --junitxml=.runtime/d-executor-harness.xml
```

## 仍属联合部署的事项

1. 在正式库显式安装视图，确定可信视图所有者，实际创建/轮换/吊销项目读取身份并分发凭据。应用不自动执行管理员 DDL。
2. 正式库安装/分发专用审计身份，并落实 outbox 保留/死信及备份/运行监测。早期审计测试使用管理员连接；随后已完成[最小权限专用审计身份、同事务权限检查与防篡改隔离验证](member-d-identities.md)。这不代替正式业务身份权限收敛或 DBA 防护验收。
3. A/B/D 替换开发身份并完成部署认证；生产环境仍按现有规则拒绝启动。外部模型/本地权重、持久化向量索引由 A/C 的后续验收负责。
4. 根据实际合并版本再做团队最终验收。当前只验证固定参考与 D 的本地组合，没有推送、修改远程 PR 或代团队批准上线；本轮无前端改动，未运行浏览器 E2E。

D 的剩余开发任务已提供实现、隔离 Provider 和可复现验证；上述部署事项不能因本地通过而标记完成。

## 前轮验证记录（645 项最新回归见末尾）

- Python 3.12.10 全量 **594 passed / 37 skipped / 0 failed**，其中实际 PostgreSQL 测试 174 项：视图/审计契约 27、执行器攻防/资源 145、团队 HTTP 流程 2。默认共享 SecurityPort 的 Mock 测试未计入这 174 项。
- 真实 SecurityHarness 的 8 项另行通过，不与全量结果相加冒充额外独立用例。37 项跳过仍为 31 项历史 SQLite 实验和 6 项默认 Mock 数据层真实库门槛，未计为通过。
- Ruff 检查及 77 个文件格式、OpenAPI、C/D Mock 示例、依赖一致性通过；wheel 已构建并验证执行器导入、可选驱动声明和 SQL 资源。报告和 wheel 位于 Git 忽略的 .runtime。
- 保留既有 Starlette/httpx TestClient 弃用提示；没有新测试警告。测试容器自动清理，不保存数据库密码，不修改业务库。

## 仅前三项补充交付后的验证

用户限定只完成独立审计身份、服务角色生命周期和可运行可信组合入口。新增 27 项真实身份/管理 CLI 验证、9 项真实启动/失败清理/独立进程验证、14 项配置解析测试，并为读取身份补一项转授权拒绝回归。实现与命令见[身份说明](member-d-identities.md)和[启动说明](member-d-startup.md)。

Python 3.12.10 全量 **645 passed / 37 skipped / 0 failed**，其中实际 PostgreSQL 用例 211 项：原视图/审计契约 27、执行器 146、原团队流程 2、身份生命周期 27、启动组合 9。报告 `.runtime/d-operations-combined-results.xml`。跳过口径仍为历史 SQLite 31 项与默认 Mock 数据层门槛 6 项，不计为通过。Ruff 及 86 文件格式、公开契约、Mock 示例、pip check 通过；wheel 已验证新增模块与 002 登记表资源。

本轮没有浏览器、正式数据库部署、生产认证、outbox 策略或外部模型验收；没有推送或修改远程 PR。管理命令实际运行仅针对自行创建并自动清理的隔离容器。
