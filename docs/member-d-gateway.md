# 成员 D 任务 3：自然语言统计网关的 AST 校验

2026-10-28 已依据 A 的正式模型完成第二步的双视图、PostgreSQL/psycopg 参数与独立项目登录适配，见[接入约定与复现](ad-security-contract-proposal.md)。审计和执行器仍待后续实现，应用真实 SQL 继续关闭；进度见[成员 D 当前进度](member-d-progress.md)。

本阶段交付自然语言路由、D 私有候选生成入口、严格 SQL AST 允许清单、可信项目参数绑定和公开结果状态。复用任务 1/2 的 SecurityPort、授权与注册；不修改 AIPort、DataPort、HTTP Schema 或前端。受限数据库执行器属于下一项任务，本阶段不执行数据库查询。

## 实现与候选来源

- `backend/app/security/sql_policy.py`：固定意图路由、严格解析器、AST 校验与 `QueryPlan(sql, parameters)`。
- `backend/app/security/adapter.py`：在合法项目上下文内生成候选，生成后重新校验成员关系，再调用 AST 策略。
- `backend/tests/test_security_gateway.py`：语法、参数绑定、恶意候选、故障与 HTTP 边界测试。

默认生成器仅精确支持以下三条问题（允许首尾空白），没有真实 LLM，不接受用户提交的 SQL、Context 或凭据。

| 自然语言问题 | 唯一允许的内部查询形状 |
|---|---|
| 按年份统计论文数量 | SELECT year, COUNT(*) AS count FROM paperpilot_security.paper_statistics GROUP BY year ORDER BY year |
| 按研究主题统计论文数量 | SELECT topic, COUNT(DISTINCT paper_id) AS count FROM paperpilot_security.paper_topics GROUP BY topic ORDER BY topic |
| 查询尚未完成证据提取的论文 | SELECT paper_id, title FROM paperpilot_security.paper_statistics WHERE has_evidence = FALSE ORDER BY paper_id |

`SecurityAdapter(data, candidate_generator=...)` 可由可信组合入口注入异步生成函数，签名为 `(Context, str) -> Awaitable[str | None]`。这只是 D 私有配置边界，没有新增 HTTP SQL API，也不假定 C 已实现 NL2SQL。所有生成器输出都重新经过相同策略；None 表示问题不支持，异常映射为固定脱敏错误。

## SQL 允许清单与绑定规则

采用 SQLGlot 30.24.0 的词法与 AST 解析（[官方文档](https://sqlglot.com/sqlglot.html)），同时在 pyproject 和锁文件精确固定版本。现使用 PostgreSQL 方言生成 psycopg 命名参数查询计划，已通过 A 固定版本真实迁移的对照测试；执行器不会随此变更自动启用。

1. 候选必须是字符串，长度 1～4096，最多 128 token；解析最多 128 AST 节点。
2. 必须为单条 SELECT；允许单个结尾分号。拒绝注释、多语句、命令回退、写入、CTE、JOIN、子查询、UNION、锁、任意函数等所有未批准结构。
3. 仅允许上表三棵完整 AST。视图、列、别名、聚合、条件和排序都必须匹配；允许按年份 COUNT(*) 与按主题 COUNT(DISTINCT paper_id)，普通 SELECT * 不允许。不扩展匹配其它“看起来只读”的查询。
4. 比较 AST 序列化中的完整结构，只忽略源码位置元数据；不依赖 SQLGlot 基于哈希的表达式相等判断，也不通过 SQL 字符串黑名单判定安全。
5. 通过后从可信模板重建查询，再以 AST 增加项目条件和 LIMIT 100，按 PostgreSQL 方言输出 `project_id = %(project_id)s`。项目值只取自服务端 Context，以独立参数字典传递，不拼进 SQL。已有 has_evidence = FALSE 与项目条件用 AND 连接。
6. 解析器拒绝不支持语法，不走默认的 Command 回退，也不记录候选文本。解析与生成器错误不会向 HTTP 响应输出 SQL、视图名、连接详情或内部异常。

`QueryPlan` 只存在于 D 模块内；本阶段校验后不保存、不返回该计划，不提供外部执行入口。后续受限执行器应消费计划中的 SQL 与参数，不能重新执行原候选字符串。

## 结果与故障

| 场景 | decision / execution_status | reason 或安全错误 |
|---|---|---|
| 允许清单通过 | allowed / not_executed | QUERY_EXECUTION_DISABLED：受限执行器尚未接入 |
| 自然语言问题不支持 | rejected / not_executed | UNSUPPORTED_QUERY |
| SQL 候选不符合策略 | rejected / not_executed | SQL_POLICY_REJECTED |
| 输入不符合现有 Schema | 无业务结果，HTTP 422 | VALIDATION_ERROR |
| 生成器/策略内部故障、权限查询不可用 | 无业务结果，HTTP 503 | GATEWAY_UNAVAILABLE，脱敏且无降级 |
| 非成员或生成期间被撤权 | 无业务结果，HTTP 404 | RESOURCE_NOT_FOUND |

当前策略标识为 `postgres-statistics-v2`（初版为 statistics-ast-v1），结果沿用 request_id，明确 `mock=false`；这只说明由 D 适配器生成，A/C 与开发身份仍可能为 Mock。只有 executed 才能携带结果，此阶段所有响应均无 columns/rows 数据。B 原有 requested/finished 业务审计继续保留；D 的执行级关键审计尚待后续接入。

## 与 A/D 的后续接入要求

初版单一 security_paper_statistics 草案已被双视图替代：D 的显式 DDL 位于 `backend/app/security/migrations/001_statistics_views.sql`，在 A 的 `0b2953c` 固定迁移上验证，但未安装到业务库。实施及权限边界见[现行约定](ad-security-contract-proposal.md)。

- 论文视图每个有效导入身份一行，主题视图每个论文/主题关联一行；按主题 DISTINCT paper_id 防重复。排除已移出论文；保留年份空值组；证据按同项目同论文 evidence 结果存在性判断，不代表提取质量。
- 已验证 PostgreSQL/psycopg 参数和双视图名称。正式部署时仍需核对视图所有者、角色生命周期与凭据分发；没有兼容视图时不得自动转查业务表。
- 受限只读数据库身份、适用的 RLS、不可绕过的项目隔离、连接池上下文复位，以及数据库端 3 秒超时、100 行和响应体限制。AST 的 LIMIT 不是数据库权限或资源限制验收。
- 执行前/后关键审计与失败关闭、事务/outbox 和资源清理；上述门槛落实之前，维持 not_executed。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_security_gateway.py backend/tests/test_security_authorization.py backend/tests/test_security_adapter.py backend/tests/test_module_registration.py backend/tests/security_contracts backend/tests/test_api.py -q
.\.venv\Scripts\python.exe -m ruff check backend/app/security backend/tests/test_security_gateway.py backend/tests/test_security_authorization.py backend/tests/test_security_adapter.py
.\.venv\Scripts\python.exe -m ruff format --check backend/app/security backend/tests/test_security_gateway.py backend/tests/test_security_authorization.py backend/tests/test_security_adapter.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
```

AST 测试包含 44 条拒绝样例、21 条合法语法变体，以及跨请求项目参数、生成器注入/故障、生成期间撤权、输入结构和 HTTP 边界。这是策略单元验证，不能作为真实测试数据库 40+20 攻防实验或 RLS 验收。共享 security_contracts 默认仍验证 Mock，不提供伪造 executed 成功的 provider。

2026-10-26，本轮在 Windows、Python 3.14.3 隔离环境验证结果：

- 上述网关、授权、注册、共享 Mock 契约及 API 组合为 **232 passed**，其中任务 3 新增 88 项测试。保留已有 Starlette/httpx TestClient 弃用提示。
- Ruff 静态及格式检查、OpenAPI 与 C/D Mock 示例一致性检查、pip check 和 git diff --check 均通过。
- 额外加入 `test_contracts.py`、`test_module_examples.py` 的组合为 **249 passed, 1 failed**。失败为已有 `test_data_port_examples_cover_current_signatures`：Python 3.14 将 `Optional[Literal['owner', 'member']]` 的签名显示为 `Literal['owner', 'member'] | None`，与 Python 3.12 基线样例的逐字比较不同。
- 已用 HEAD 基线的 ports.py 和 data-port-examples.json 在同一解释器复现该差异，并确认端口、样例与该测试文件均未改动。未为消除环境差异重写 A/B 契约或样例；Python 3.12 下仍需复验。
- 未连接实际数据库、收费模型或启动外部服务；未执行 SQL、真实数据库攻防及前端测试。授权策略已单独提交为 cf2f85c。

同日后续修复：Python 3.14 签名表示误报已消除。`backend/tests/signature_contracts.py` 用 Python AST 比较参数/默认值/注解结构，只在类型注解内统一 Optional、Union 和 `|` 的等价形式；不 eval/exec 样例内容，不改公开端口和现有 JSON。A 的签名测试与 C/D 的签名/生成示例比较复用该辅助函数；其它示例字段仍完整比较。

修复后的 `test_signature_contracts.py + test_contracts.py + test_module_examples.py` 为 **44 passed**，包含 26 项签名回归：等价类型表示可通过，参数名称/顺序/种类/默认值、嵌套类型与返回类型变化仍会失败。Ruff 和差异检查通过。上面的 249 passed / 1 failed 保留为修复前记录；Python 3.12 本轮未实际复验。


## 2026-10-27：Python 3.12 基线复验

项目开发与 Docker 基线为 Python 3.12，`pyproject.toml` 声明 `requires-python = ">=3.12"`，Ruff 目标为 py312；并非只允许 3.12。前文 3.14 的记录保留为历史，当前已完成以下 3.12 实测。

在 Windows x64 使用官方 Python 3.12.10：通过已有 Python 安装管理器的 [target 隔离解压方式](https://docs.python.org/3/using/windows.html#installing-runtimes) 放入 `.runtime/python312`，创建 `.runtime/venv312`，安装原 `requirements.lock` 及 `--no-deps -e .` 本地项目。原 `.venv` 与系统默认 Python 3.14.3 均保留；管理器执行时自动更新至 26.3。未改锁文件或业务代码。

```powershell
.\.runtime\venv312\Scripts\python.exe --version
.\.runtime\venv312\Scripts\python.exe -m pytest -q -rs --junitxml=.runtime/python312-default-results.xml
.\.runtime\venv312\Scripts\python.exe scripts/export_contracts.py --check
.\.runtime\venv312\Scripts\python.exe scripts/export_module_examples.py --check
.\.runtime\venv312\Scripts\python.exe -m ruff check backend scripts
.\.runtime\venv312\Scripts\python.exe -m ruff format --check backend scripts
.\.runtime\venv312\Scripts\python.exe -m pip check
```

结果：

- 默认后端全量测试 **344 passed, 37 skipped, 0 failed**，16.23 秒；签名兼容性和 A/C/D 契约相关 44 项均包含在此结果内，不重复累加。
- 跳过项为 31 项历史 SQLite 显式 opt-in 实验，以及 6 项尚需正式数据库 provider 的真实数据库门槛，未计作通过。
- OpenAPI、C/D Mock 示例一致性、后端及脚本 Ruff 静态检查、64 个文件格式检查、依赖一致性和 git diff --check 全部通过。
- 保留 1 条已有 Starlette/httpx TestClient 弃用提示，无测试失败；真实数据库、前端和正式 SQL 攻防本轮未运行。
- JUnit 报告、运行时及虚拟环境均位于 Git 忽略的 `.runtime`，可使用上述解释器路径继续复验。本次未提交或推送。

## 2026-10-28：正式模型与 PostgreSQL 第二步适配

已同步 B PR #5（main@177dddc），新增双视图 DDL、PostgreSQL 参数绑定及 A/D 隔离验证；原 Mock 示例与公开契约不变。最新实测与复现方式见[接入约定](ad-security-contract-proposal.md)。上面的 2026-10-26/27 数字均保留为历史记录，不与本轮测试累加。
