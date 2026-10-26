# 成员 A 交接摘要

## Phase 3：当前开工入口

### 1. 职责范围

负责正式数据库选型提案、关系模型、持久化、迁移、事务/约束/索引、并发恢复与性能实验。选型由 A/团队决定，B 不指定引擎。保持 AB-1，提供可替换的 DataPort。

持久化对象及关联：用户 → 项目成员/角色 → 项目；项目 → 项目论文关联 → 论文元数据/摘要/文件引用；任务 → 所属项目/操作者/论文集合/尝试次数/结果引用；论文 → 来源片段/页码 → 结构化证据；项目 → 报告 → 不可覆盖版本/引用快照；业务请求 → 审计事件。paper_id 当前表示一次项目导入身份，不是全局共享文献权限。PDF 由 B 文件存储管理，完整正文/向量与 C 协商，不能将 Source 当作完整向量模型。

### 2. 代码入口与建议位置

- 定义：`backend/app/adapters/ports.py`、`schemas.py`、`adapters/data_rules.py`。
- 精确输入、缺失、冲突、事务/权限语义：[AB-1](ab-data-contract.md)；26 方法可执行样例：`contracts/data-port-examples.json`。
- 注册：`DataBinding`、`adapters/factory.py`、`main.create_app(..., data_binding=...)`。
- 建议新增 `backend/app/data/`（尚不存在，A 自行组织）；原 `persistence/` 为保留的 SQLite 历史实验，非正式模型，不自动注册。无需修改 B 的 services/routes/前端。

### 3. 完整方法清单（26）

以下 Ctx 指可信 Context(user_id:UUID, project_id:UUID, request_id:UUID)，所有标识为 UUID；完整类型以 ports.py 为准。

| 方法及输入 | 返回 |
|---|---|
| user_by_name(username:str) | User 或 None |
| user_by_id(user_id:UUID) | User 或 None |
| role(user_id:UUID, project_id:UUID) | owner/member 或 None |
| projects(user_id:UUID) | list[Project] |
| create_project(user_id:UUID, data:ProjectCreate, request_id:UUID) | Project |
| members(ctx:Ctx) | list[Member] |
| add_member(ctx:Ctx, username:str) | Member |
| remove_member(ctx:Ctx, user_id:UUID) | None |
| papers(ctx:Ctx, year_from:int或None=None, year_to:int或None=None) | list[Paper] |
| paper(ctx:Ctx, paper_id:UUID) | Paper |
| sources(ctx:Ctx, paper_id:UUID) | list[Source] |
| evidence(ctx:Ctx, paper_id:UUID) | list[EvidenceResult] |
| remove_paper(ctx:Ctx, paper_id:UUID) | None |
| import_paper(ctx:Ctx, title:str, year:int或None, digest:str, file:FileReference, key:str) | tuple[Paper,Task] |
| create_task(ctx:Ctx, kind:TaskKind, payload:TaskPayload, key:str) | Task |
| task(ctx:Ctx, task_id:UUID) | Task |
| retry_task(ctx:Ctx, task_id:UUID) | Task |
| claim_job() | Job 或 None |
| complete_job(job:Job, result:JobResult) | None |
| fail_job(job:Job, code:str, message:str) | None |
| save_report(ctx:Ctx, data:ReportSave, key:str) | ReportVersion |
| versions(ctx:Ctx, report_id:UUID) | list[ReportVersion] |
| reports(ctx:Ctx) | list[ReportVersion] |
| controlled_statistics(ctx:Ctx, intent:StatisticsIntent) | tuple[list[str],list[dict]] |
| append_audit(event:AuditEvent) | None |
| audit(ctx:Ctx, *, action,decision,actor_id,since,until,offset,limit) | AuditList；全部关键字参数必填，可空项类型见 AB-1 |

TaskKind=process/answer/evidence/draft，StatisticsIntent=by_year/by_topic/missing_evidence；不接受任意 SQL。生命周期、迁移、种子初始化和 readiness 不加入 DataPort。

### 4. 正常请求与响应示例

以下来自现有 `data-port-examples.json` 的 create_project，全部为合成 Mock 示例；内部方法接收对象，JSON 是序列化展示，不是新 HTTP 接口。

```json
{"user_id":"d9f21e57-118e-5387-8c77-36ab7e27e4cc","data":{"name":"A/B 交接示例","description":"合成接口数据"},"request_id":"96489693-5d8f-4c17-b03f-c51415a8a161"}
```

```json
{"project_id":"1b073236-1dfb-4fd2-ab6d-9e12a2a969f7","name":"A/B 交接示例","description":"合成接口数据","owner_id":"d9f21e57-118e-5387-8c77-36ab7e27e4cc","role":"owner","mock":true}
```

### 5. 异常示例及事务边界

同一示例将 user_id 改为不存在的 `69afe10d-d1bb-439a-8433-7b4019594df6`，实际抛 AppError，导出如下；内部没有 request_id，HTTP 层会补齐。

```json
{"status":404,"code":"RESOURCE_NOT_FOUND","message":"资源不存在或不可访问"}
```

导入的论文/项目关联/处理任务/幂等记录原子提交；报告正文/递增版本/引用快照同事务；task_id+attempts 条件提交拒绝旧 worker。已知回滚 DATA_UNAVAILABLE，确认未知 DATA_COMMIT_UNKNOWN，不允许 B 据此误删已被采纳文件。外部 AI 不在长事务内。业务成功与领域事件/outbox 的一致性和审计投递由 A/D 对齐。

### 6. 注册、启动与测试

完成实现后由 A 的可信组合入口构造 `DataBinding(adapter, check_ready, close_resources)`，传给 `main.create_app(Settings(a_mode='persistent'), data_binding=binding)`。这里的三个名称是 A 提供的对象/函数，不是现存数据库类；初始化失败须脱敏并清理自身资源，B 不执行默认迁移。未注册 persistent/real 即拒绝启动。统一启动方式见 [开发指南](development-guide.md)。

```powershell
# 现成 Mock 入口；交付 A 实现后改为 A 提供的组合入口，而不是仅改环境变量
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
# 另一终端执行测试；端口被占用时见统一指南的替代端口
# 先跑默认 Mock 行为，数据库门槛会明确跳过
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts -q -rs
# A 完成专用隔离库 provider 后运行；下面模块名需换成实际交付模块
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider your_package:build_harness -q -rs
.\.venv\Scripts\python.exe -m pytest backend/tests/test_data_binding.py -q
```

provider 协议为 `build_harness(*,tmp_path)` context manager，返回 `data_contracts.harness.DataHarness`，含 data、owner/member/outsider、kind=database、提交前实际故障探针、reopen 和 expire_claim。不得复用用户运行库或用 Mock 假装持久化。现有 41 项共享测试=35 行为+6 数据库门槛。

### 7. 向其他成员交付

向 B 交付适配器、DataBinding 构造入口、迁移/初始化/readiness/关闭说明、隔离测试 provider 与结果。向 C 交付片段/正文/向量和索引提交面；向 D 交付受限身份/视图/适用的行级隔离及审计保护能力。附约束、事务、并发和性能证据，不能只提交 ORM 类。

### 8. 验收标准

26 方法兼容；共享测试无伪造成功，真实库门槛实际运行；项目范围再授权；同摘要/版本唯一和约束失败回滚；重启恢复、领取/租约/旧 attempts 隔离；无静默回退；可独立启动并由 B 业务调用；正式数据库安全和性能另外联合验收。

### 9. 不属于 A

不修改前端、B 的 HTTP 业务与任务编排，不实现 C 的模型算法或 D 的 SQL AST/安全网关。不单独另建一套业务用户/权限来源。真实认证由 A/B/D 协商，当前开发身份不是生产认证。

### 10. 待团队确认

数据库/驱动/迁移工具、正式用户和会话、租约续期与死信、幂等保留和按键对账、空文献范围语义、文件补偿、全文/向量/主题结构、关键审计/outbox、数据库受限权限和性能规模。这些不阻止 A 按 AB-1 开始实体及事务实现。

---

以下保留 Phase 2B 历史说明；当前开工以本节和 AB-1 为准。

## 当前有效交接（Phase 2B 中立调整）

数据库选型完全交由 A/团队决定。B 的工厂已解除 persistent 与 SQLite 的默认绑定；没有显式注册真实 DataPort 时，persistent/real 均安全拒绝启动。AB-1 的 26 方法和公开语义不变。A 在可信组合入口通过 `DataBinding(data, ready, close)` 注册自身实现和独立健康检查；B 不替 A 建模、迁移或引入数据库驱动。

请复用 `backend/tests/data_contracts/`，提供专用隔离库 provider 与重连/租约恢复探针；当前有 35 个通用行为用例和 6 个实际数据库门槛，默认 Mock 不证明持久化。历史 SQLite 实验单独显式运行，不能视作正式库验收。

B 已补齐上传/报告相同载荷重试保留幂等键、未知提交保留文件、任务结果确认不明时不错误标记失败、报告历史及引用快照读取、统一脱敏错误和 readiness。请 A 确定事务/并发、幂等保留期、文件引用对账、领域审计/outbox、租约与恢复，并与 C/D 确定正文/向量和数据库权限。具体注册示例及验收命令见 `docs/persistence-integration.md`。

以下内容保留为前次交接历史，不能据此将 SQLite 当作团队选型或继续使用旧的自动注册方式。

## 先前 SQLite 实验交接（历史）

PaperPilot Secure 的 A/B 数据契约 AB-1 保持冻结，详见 `docs/ab-data-contract.md`。B 已实现 `backend/app/persistence/` 的临时 SQLite 数据层，并注册 `a=persistent,c=mock,d=mock` 开发组合；26 个 DataPort 方法参数和公开 HTTP Schema 未改，无新增数据库依赖。正式数据库选型仍请 A 与团队确认。

优先交付用户/项目/成员关系、论文导入、任务、片段与证据、报告版本及审计。每个方法的实际输入/返回/错误示例在 `contracts/data-port-examples.json`，共享行为测试在 `backend/tests/data_contracts`。

重点保证：导入的论文+项目关联+任务+幂等记录原子提交；项目内摘要唯一；任务按 task_id+attempts 防止迟到写入；报告正文+递增版本+引用快照同事务且不覆盖旧版本；数据层重新验证项目范围，不能只依赖 B 预检。业务事务同时保存领域审计或 outbox，与 D 对齐审计投递。

请将明确回滚映射为 DATA_UNAVAILABLE，提交结果未知映射为 DATA_COMMIT_UNKNOWN；后者不能让 B 删除可能已采纳的原文件。普通业务提交和外部 AI 调用不共享长事务。

共享测试现在可通过 `--data-contract-provider data_contracts.sqlite_provider:build_harness` 运行，40 项含 5 个数据库门槛均实际在隔离 SQLite 库验证。另有 SQL 约束、真实失败回滚、自然租约过期及两个 HTTP 服务进程的重启读取测试；前端持久化全流程已验证。默认 Mock 仍跳过数据库门槛。请为正式库提供独立 provider 并复验，不能将本机两线程结果视作正式库并发或生产保证。

开始前请确认：paper_id 表示项目导入身份；同项目移出后摘要仍占用，是否需要恢复另议；与 C 对齐完整正文/向量载荷、作者主题结构；与 D 对齐角色/RLS、审计/outbox；与 B 确定正式迁移、readiness、认证会话和文件补偿。

可复用：端口/错误语义、模型映射、事务边界、幂等和 attempts 条件提交思路、共享测试和重启测试。需审查替换：SQLite DDL、全局写锁、开发身份初始化和临时迁移实现。已有与业务同事务的最小 `domain_events` 日志，但没有可靠投递/消费；租约无心跳、重试上限或死信队列。没有正文/向量存储、生产密码/会话、数据库角色/RLS、真实安全网关、性能及备份恢复验收。详见 `docs/persistence.md`。
