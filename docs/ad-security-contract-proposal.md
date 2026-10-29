# A/D 统计与受限执行接入约定（D 侧实现）

日期：2026-10-28。D 侧已落地双视图、PostgreSQL/psycopg 参数和独立项目登录绑定，并完成关键审计、受限执行器和隔离验证。文件名保留以兼容已有链接。本记录不冒充 A 的人工签字，不自动修改业务库或正式角色；默认保持执行关闭，可信组合可显式注入项目读取配置启用。

## 依据与边界

已核对 A 的 [最新实现记录](https://github.com/SiriThree/PaperPilot/blob/0b2953c218668af71a4bf869e5c6d238cc0d588d/docs/data-adapter.md) 及 [controlled_statistics / append_audit](https://github.com/SiriThree/PaperPilot/blob/0b2953c218668af71a4bf869e5c6d238cc0d588d/backend/app/data/adapter.py#L962)。A 仍将受限角色、视图、RLS/可信项目上下文列为协作项，PR #1 当前没有确认这些约定的评论。

B 的 PR #5 已合入的 main@177dddc 已同步到 D。保持 AB-1 的 26 方法、SecurityPort 两方法和公开 Schema 不变；候选 SQL 只在 D 内部处理，不使用 DataPort.controlled_statistics 执行候选。测试调用 A 的该方法仅作为统计语义的对照结果。

## 1．已实现的统计视图与语义

DDL 位于 `backend/app/security/migrations/001_statistics_views.sql`，不修改 A 的 `001_initial.sql`。须由可信管理员显式安装，应用启动时不执行；已有同名 schema 时拒绝安装，不覆盖未知对象。以下对象已在 A 的固定版本真实迁移上验证，不支持请求指定表名。

| 已实现对象 | 行粒度与字段 | 实现语义 |
|---|---|---|
| paperpilot_security.paper_statistics | 每个有效项目导入身份一行：project_id、paper_id、title、year、has_evidence | paper_id 来自 project_papers，按项目和 metadata_id 连接 papers；排除 removed_at 非空；不连接主题展开行。year 可空，按年份统计保留空值组，与 A 当前实现对齐。 |
| paperpilot_security.paper_topics | 每个项目内 paper_id/topic_id 关联一行：project_id、paper_id、topic_id、topic | 来自有效 project_papers、paper_topics 和 topics；排除已移出论文；每主题按 COUNT(DISTINCT paper_id) 计数，多主题论文可在各主题各计一次。 |

has_evidence 为同项目、同论文是否存在已提交的 task_results.kind='evidence'。已有证据但字段为不足/未提及，仍视为存在；不据此宣称证据质量通过。当前没有主题写入面，主题空结果必须真实保留，不使用合成主题补齐。

两种行粒度已避免主题展开导致年份 COUNT(*) 和未提取列表重复；旧的单一 security_paper_statistics 模板不再被 AST 允许。候选只接受三棵固定 PostgreSQL AST，不直接访问底层业务表或任意 JOIN。年份和主题按对应字段排序；未提取列表按 paper_id 稳定排序（对照 A 时按记录内容比较，不依赖 A 的导入时间排序）。

## 2．驱动、身份和项目隔离

- 使用 A 当前 PostgreSQL 16 + psycopg 3.3.6。QueryPlan 已输出 PostgreSQL 方言和 `%(project_id)s` 命名占位符，项目值独立放在参数字典中，经真实 psycopg execute 验证；不拼接用户/项目值，也不再输出中立 :project_id 文本。参见 [psycopg 参数文档](https://www.psycopg.org/psycopg3/docs/basic/params.html)。
- 查询连接必须使用独立最小权限登录身份：不是 postgres、表所有者或超级用户，无 BYPASSRLS、建库、建角色和 DDL 权限，不继承业务写角色；读取范围仅限批准统计字段。
- 项目来自 B 的可信 Context，D 在调用前重新检查当前成员关系。数据库层还必须阻止该读取身份直接跨项目读取，不能只依赖候选中的 WHERE。
- 已按 A 的 day0 独立登录方向实现“一个受限数据库登录绑定一个项目”：管理员维护 `paperpilot_security.project_readers(login_role, project_id)`，读取身份不能查询或修改该表；视图用 SESSION_USER 查询绑定，未绑定登录返回空结果。两个 security_barrier 视图依据视图所有者权限连接底表，读取登录仅授予 schema USAGE 和两个视图 SELECT，不授予底表访问。依据 [PostgreSQL 16 视图权限文档](https://www.postgresql.org/docs/16/sql-createview.html)，不将其误称为已改造 A 底表的原生 RLS 策略。
- 已实测读取登录无超级用户/BYPASSRLS/建库/建角色权限；无应用过滤也只见绑定项目，伪造项目参数或自设项目 GUC 不越界，不能 SET ROLE 到另一项目或 SET SESSION AUTHORIZATION 到管理员。撤销绑定后既有连接也不再看见数据。正式项目创建/移除时的账号、凭据分发和吊销属于后续执行器/联合部署，不由应用自动创建角色。
- 每次查询独立只读事务，资源配置和角色选择只来自可信组合入口。会话设置限于本事务；失败、取消或超时后先确认回滚/关闭，连接状态不确定时不得归还供后续请求使用。

## 3．执行级关键审计（D 门槛已实现）

复用 A 的 append_audit 和 event_id 幂等语义，不新建第二套用户/审计表或重复开发 outbox 投递器。

1. 执行前追加 D 的关键事件并确认成功；审计不可用时不调用执行器。
2. 执行成功后追加结果状态事件并确认成功，再向 B 返回结果；确认失败不返回行数据，不声称此前 SQL 没有运行。
3. 执行异常、超时和取消也记录对应事件；错误脱敏，审计中不保存候选 SQL、凭据和未授权内容。request_id 关联整个请求，事件使用各自的 event_id。
4. 只读 SQL 身份与审计写入权限分离。审计连接/受控写入身份的配置及保护由 A/D 对齐，不给统计读取身份直接修改审计和 outbox 的权限。

A 的业务 outbox 为独立异步投递，B 的 requested/finished 为业务日志，均不能替代执行前的确认门槛。现有 published 无限保留策略先保持；本草案不新增自动删除、过期或死信清理。保留/死信策略后续单独确认。

2026-10-28 已在 SecurityAdapter 落地上述门槛，事件为 security.query.prepared 与 security.query.finished；拒绝、默认关闭、故障和取消也有对应完成状态。审计确认失败返回脱敏 AUDIT_UNAVAILABLE，不执行或不返回结果，不自动重跑 SQL。A 的真实 append_audit 提交、回滚和 event_id 幂等已有独立 PostgreSQL 验证；正式最小权限审计写身份仍待部署。内部可控执行依赖仅用于验证，默认工厂没有 SQL 执行器。事件语义、复现和限制见 [任务 6 说明](member-d-audit.md)。

## 4．结果与资源限制（已实现并在隔离库验证）

- 已实现数据库端 statement_timeout=3 秒、SQL LIMIT 100 与应用 3 秒等待预算；超时/取消发送数据库取消请求并等待工作线程关闭连接，不遗留供后续请求复用的会话。清理和审计耗时不等于 3 秒内响应保证。
- 已实施完整响应序列化 UTF-8 大小硬上限 256 KiB，包含元数据；超过上限安全失败，不返回部分结果，不擅改公开 Schema。
- columns/rows 只含批准字段与公开 Schema 的基础类型；UUID 等驱动对象在 D 边界转换，内部 SQL、异常正文与连接对象不得返回。
- 只有执行完成且关键审计已确认才返回 allowed/executed；拒绝为 rejected/not_executed，真实执行故障为 undetermined/failed，网关不可用为脱敏 AppError。未接入时继续 not_executed，不降级 Mock。

可信组合使用 build_security(data, readers=...) 启用 PostgresQueryExecutor，未配置时保持校验模式。身份/权限运行时检查、只读事务、驱动类型及错误代码见[执行器说明](member-d-executor.md)；共享 Provider、44+21 攻防与 A+D/A+C+D HTTP 组合见[验证记录](member-d-security-validation.md)。正式业务库安装、角色与审计写身份仍未部署。

后续已补充可部署的身份/角色管理实现：独立审计绑定通过 A append_audit 追加并在提交前检查 ACL，管理工具负责登记身份开通/轮换/撤销；严格可信配置由 app.security.bootstrap 组合 A+D/A+C+D。见[身份说明](member-d-identities.md)和[启动说明](member-d-startup.md)。这些实现已在独立库验证，但仍未操作正式库。

## 5．隔离验证与复现

`backend/tests/test_security_postgres_contract.py` 显式 opt-in，固定参考 A 的 `0b2953c218668af71a4bf869e5c6d238cc0d588d`，不把 A 未合并源码复制进 D 或自动加载到业务启动路径。测试临时将该参考加入 app 的包搜索路径，使用 A 的 PostgresDatabase 迁移、PostgresData 和公开方法创建项目/论文/证据；主题关联因公共端口暂无写入方法，仅在管理员测试夹具中建立。

测试自行创建 `paperpilot-d-step2-*` 独立临时容器，使用 A 相同的 pgvector 镜像摘要、随机仅回环端口和内存生成的随机凭据。测试登录与管理员分离；结束后停止且自动删除自己创建的容器，不接触用户业务库，不创建 day0.env，不复用已存在容器。

```powershell
git fetch origin member-a
# 若该参考工作树已存在，保留并检查其 HEAD 为下列固定提交，不覆盖用户目录。
git worktree add --detach .runtime/member-a-step2-reference 0b2953c218668af71a4bf869e5c6d238cc0d588d
.\.runtime\venv312\Scripts\python.exe -m pip install -r requirements.lock -r requirements-security-test.lock
$env:PAPERPILOT_SECURITY_POSTGRES_TESTS='1'
$env:PAPERPILOT_SECURITY_TEST_A_SOURCE=(Resolve-Path '.runtime/member-a-step2-reference').Path
.\.runtime\venv312\Scripts\python.exe -m pytest backend/tests/test_security_postgres_contract.py -q
```

要求 Docker 已运行，首次需能拉取固定镜像。默认 pytest 不开启该组测试，明确跳过；显式开启但缺依赖、Docker、正确 A 参考或验证失败则报错，不冒充通过。只有测试管理员可以安装迁移/视图、建立角色和绑定；不能用该管理员连接作为被测读取身份。

2026-10-28 第二步在 Windows / Python 3.12.10 实测：全量后端 **401 passed、37 skipped、0 failed**，其中本组 22 项确实连接 PostgreSQL 16 执行，没有计入跳过项。剩余跳过为 31 项历史 SQLite 实验和 6 项默认 Mock 数据层 provider 门槛；本组不替代这些 provider 测试。JUnit 报告为 Git 忽略的 `.runtime/d-step2-combined-results.xml`，记录数据库版本、A 提交及镜像摘要。随后第三步加入关键审计验证，全量 **430 passed / 37 skipped**，其中 PostgreSQL 27 项，报告 `.runtime/d-step3-combined-results.xml`，详见[任务 6](member-d-audit.md)。保留既有 Starlette/httpx 弃用提示；公开契约、C/D Mock 示例、Ruff 和依赖检查通过。

## 6．完成边界

第二步的 D 侧统计/驱动/隔离约定已有代码与真实 PostgreSQL 对照证据，不再因没有 A 的人工评论而阻塞独立实现。正式部署时仍需 A/D 共同核对角色生命周期、视图所有者、审计写入身份和业务库安装窗口；未改 A 的原始迁移，未替团队批准生产部署。

关键审计、受限执行器/结果映射、真实 SecurityHarness、44+21 攻防以及 A+D 再 A+C+D 本地 HTTP 组合已按顺序完成。默认不配置读取身份仍为 allowed/not_executed；显式可信配置后可以执行。实际验证边界和剩余联合部署事项见[验证记录](member-d-security-validation.md)，不将本地通过当成生产验收。
