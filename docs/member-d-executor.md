# 成员 D 任务 4/5/7：受限执行器与真实测试 Provider

2026-10-28。D 已提供 PostgresQueryExecutor、结果/错误映射及真实 SecurityHarness。默认 build_security(data) 保留校验模式；可信组合入口显式传入项目读取身份后，查询经审计门槛实际执行。不修改 SecurityPort、DataPort、公开 Schema、前端或 A 的迁移。

## 组合与身份

安装 `pip install -e '.[security]'`，或使用锁文件中的 psycopg/psycopg-binary 3.3.6。ReaderConfig 的密码不进入 repr；配置只由服务端秘密配置提供，不接受 HTTP 参数，不写入仓库。

```python
from functools import partial
from app.main import create_app
from app.security import ReaderConfig, build_security

# settings 必须显式设置 a_mode=persistent、d_mode=real。
# binding 由 A 的 compose_data_binding 创建；readers 来自可信秘密配置。
readers = {
    trusted_project_id: ReaderConfig(
        host=database_host, port=database_port, dbname=database_name,
        user=project_reader_user, password=project_reader_password,
    )
}
app = create_app(settings, data_binding=binding,
                 security_factory=partial(build_security, readers=readers))
```

C=real 时，将同样的 binding 与 security_factory 传给 C 的 app.ai.bootstrap.create_app。固定参考 A/C 的独立组合测试见[验证记录](member-d-security-validation.md)。没有配置时返回 QUERY_EXECUTION_DISABLED；显式传空映射或配置形状错误拒绝构造，缺项目/错误登录/权限不合格返回 GATEWAY_UNAVAILABLE，不转用管理员、DataPort.controlled_statistics 或 Mock。

管理员先安装 A 正式迁移，再显式安装 D 的 migrations/001_statistics_views.sql。每个项目创建新的 LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION 角色，无其他角色成员关系；绑定 login_role 与 project_id，只授予 paperpilot_security schema USAGE 和两个视图 SELECT。须撤销 PUBLIC 在数据库上的 CREATE/TEMPORARY 和业务 schema CREATE；这属于管理员部署操作，应用不会自行修改业务库权限。

执行器每次检查实际 session_user/current_user、角色危险标志、角色成员关系、数据库/业务 schema 创建权限、底表/列/序列/额外视图权限、可执行 SECURITY DEFINER 函数以及两视图的 security_barrier 和所有者。读取身份有业务写入、审计/outbox/绑定表读取权限或权限漂移时均拒绝查询。两个视图按 SESSION_USER 绑定过滤，SQL 另绑定可信 Context.project_id；绑定错误或撤销后不会返回其他项目数据。视图定义、所有者和管理员本身属于可信部署边界；没有把 A 底表改造成原生 RLS。

项目读取角色的开通、密码轮换、分发与停用由管理员负责：先准备新角色和绑定，再通过可信配置替换该项目 ReaderConfig；停止发放旧配置，撤销旧绑定并停用登录，确认旧会话退出后再清理角色。执行器不保留会话；撤销成员仍由 A 的最新成员关系控制。正式身份/凭据生命周期尚未在业务库操作。

## 执行、结果和资源

- 执行器边界再次比对三棵正式模板、项目参数和 LIMIT 100，从可信模板重建独立参数快照；直接伪造 QueryPlan 或修改项目参数也不能绕过。
- 每次查询新建独立 psycopg 连接，使用 BEGIN READ ONLY；固定 search_path=pg_catalog、statement_timeout=3000ms、lock_timeout=1000ms、idle_in_transaction_session_timeout=3000ms。没有跨请求连接池或可残留的项目 GUC。
- 在 asyncio.to_thread 内运行同步驱动，兼容 Windows 默认事件循环。应用等待预算为 3 秒；超时或取消发送 cancel_safe，等待工作线程退出并关闭连接，重复取消也不跳过清理。成功显式回滚只读事务，异常路径关闭连接并丢弃事务。取消或网络清理耗时不计为“3 秒内必定返回”的保证；审计 I/O 也不在此 SQL 预算内。依据 [psycopg 取消文档](https://www.psycopg.org/psycopg3/docs/api/connections.html#psycopg.Connection.cancel_safe)。
- 使用服务端游标逐行读取，最多 100 行；累积结果字节超限即失败。UUID 转字符串，仅允许基础 JSON 类型且拒绝非有限浮点数；列只允许三个正式统计输出形状。
- 返回前检查完整 SecureQueryResult 的 UTF-8 序列化大小，硬上限 256 KiB，包含列名、元数据和 JSON 开销。超限不返回部分结果。100 行是 SQL 结果上限，不是“查询到了第 101 行”的分页承诺。
- 执行前确认关键审计，执行结束后确认关键审计，再复查成员关系后返回。finished/executed 表示 SQL 已完成，不表示客户端一定收到了行；完成审计期间被撤权仍阻止返回。审计不保存 SQL、问题、结果或凭据。

| 场景 | 结果 |
|---|---|
| 完成且审计确认、仍有权限 | allowed/executed，mock=false |
| 未启用执行 | allowed/not_executed，QUERY_EXECUTION_DISABLED |
| 问题/候选拒绝 | rejected/not_executed |
| 真实数据库操作失败、结果不符合契约 | undetermined/failed，QUERY_EXECUTION_FAILED |
| 数据库/应用超时 | undetermined/failed，QUERY_TIMEOUT |
| 行数/序列化大小超限 | undetermined/failed，QUERY_RESULT_TOO_LARGE |
| 缺读取身份、连接失败或权限配置不安全 | HTTP 503 GATEWAY_UNAVAILABLE |
| 关键审计未确认 | HTTP 503 AUDIT_UNAVAILABLE，无行 |
| 任务取消 | 关闭数据库连接后传播 CancelledError，无行 |
| 期间撤权 | RESOURCE_NOT_FOUND，无行；不声称 SQL 未发生 |

## 隔离 Provider

backend/tests/security_contracts/postgres_provider.py 复用已验证的一次性 PostgreSQL 夹具，固定 A 的 `0b2953c218668af71a4bf869e5c6d238cc0d588d`。build_harness 是 context manager，提供实际 SecurityAdapter/PostgresQueryExecutor/A DataPort；普通失败来自真实表锁超时，不可达来自错误凭据。管理员仅供测试初始化、观测与 A 数据写入，不能冒充统计读取身份。

```powershell
$env:PAPERPILOT_SECURITY_TEST_A_SOURCE=(Resolve-Path '.runtime/member-a-step2-reference').Path
.\.runtime\venv312\Scripts\python.exe -m pytest backend/tests/security_contracts --security-contract-provider security_contracts.postgres_provider:build_harness -q
```

8 项真实共享契约通过。每个共享测试模块复用一个独立容器，退出自动清理。显式选择 provider 后缺依赖、Docker 或正确 A 参考直接报错；未指定 provider 仍测试 Mock，不能混计。正式审计写身份和防篡改部署、生产认证及外部模型服务验收见[剩余部署事项](member-d-security-validation.md)。

随后已提供[独立最小权限审计身份与服务角色管理](member-d-identities.md)及[可信配置/组合启动入口](member-d-startup.md)。实际运行优先使用 app.security.bootstrap:create_app；原手工注入示例仅展示底层组合形状。读取身份也拒绝转授权能力，新增审计身份在提交前复查 ACL；没有自动在正式库安装角色。
