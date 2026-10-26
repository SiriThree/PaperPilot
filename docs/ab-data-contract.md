# A/B 数据访问契约 AB-1

日期：2026-10-26。状态：B 侧代码与共享行为测试基线已冻结，供 A 实现；团队未决事项见末节。本文不表示真实数据库已接入，也不替代 A/D 的数据库权限验收。

定义源：`backend/app/adapters/ports.py`。本次保留全部 **26** 个 DataPort 方法、参数名称、位置、必填条件和默认值；仅将宽泛注解收窄为现有合法值。没有新增 HTTP 操作，没有修改公开 Schema 或前端类型。内部错误和审计阶段语义的兼容性说明见后文。

所有方法完整输入和实际返回示例见 `contracts/data-port-examples.json`，按方法名索引；含正常值以及缺失/拒绝/冲突示例。该文件由 `scripts/export_data_examples.py` 实际调用 Mock 生成，测试检查方法覆盖与签名一致。内部 AppError 不含 request_id，HTTP 层根据当前请求补齐。

## 实际审查结果

| 业务能力 | 审查前状态 | AB-1 处理及剩余边界 |
|---|---|---|
| 用户、项目、成员和角色查询 | 已有且满足最小开发流程 | 保留方法；返回分离副本，未知用户/角色语义明确；真实认证仍需 A/B/D 协商 |
| 创建项目、添加及移除成员 | 已有但语义不足 | 创建者必须存在；负责人关系原子创建；添加重复成员不降级角色；负责人不能移除 |
| 论文、项目关联及年份筛选 | 已有但语义不足 | 明确 paper_id 的项目导入语义；补充编号冲突、过滤及直接数据访问授权；作者/主题补充需 A/C 协商 |
| 文件位置、摘要和重复导入 | 已有但语义不足 | B 保存文件、计算 SHA-256；A 持久化引用和摘要并约束唯一；拒绝跨项目文件引用 |
| 任务创建、提交、状态及安全重试 | 已有但语义不足 | 明确载荷/结果类型，规范化指纹、状态比较、单调 attempts 和迟到写入保护；真实租约/恢复待 A |
| 片段、页码、来源和证据持久化 | 已有但语义不足 | complete_job 原子写入并校验项目、论文、引用；完整正文、向量维度和索引载荷需 A/C 另行确定 |
| 报告版本与引用快照 | 已有但语义不足 | 数据层再次校验引用；CAS、幂等及不可变快照；失败不得留下半个版本 |
| 受控统计 | 已有但语义不足、需要 A/D 协商 | 固定三个意图；修正 Mock 的“未提取”过滤；主题当前仍是合成演示，真实视图/角色由 A/D 冻结 |
| 审计追加与分页 | 已有但语义不足、需要 A/D 协商 | event_id 去重，稳定分页、负责人校验；真实事务内审计/outbox 由 A/D 落实 |
| 数据库健康检查和初始化 | 缺失（数据层生命周期能力） | 当前 /health 仅应用存活；迁移/种子脚本由 A 提供，readiness 在真实集成时另定，不扩大 DataPort |

## 核心实体与关联

- User → ProjectMember → Project：项目与用户多对多，负责人也是唯一 owner 成员。一个项目只有一个负责人；AB-1 不支持负责人转让。
- Project → ProjectPaper：每次项目导入具有独立公开 paper_id。当前 Paper 的 project_id 指向唯一所属项目；同一文件导入两个项目获得不同 paper_id。A 可内部复用书目元数据，但不得让公开 paper_id 或片段绕过项目边界。
- ProjectPaper 保存文件摘要、文件存储引用、title、可空 year 和处理状态。作者/主题可由 A 扩展内部关联，但本基线没有相应写入字段。
- ProjectPaper → Chunk/Source → Evidence：片段归属导入记录；Source 是来源视图，不是完整正文/向量存储模型。EvidenceResult 保存五类字段和支撑来源。
- Project → Task：任务保存可信 Context、类型、载荷、状态、attempts、结果/错误和时间。任务引用论文必须处于同一项目。
- Project → Report → ReportVersion → SourceSnapshot：版本按 number 递增；正文、问题及来源快照属于同一版本。移出论文保留历史快照，禁止公开访问。
- AuditEvent 通过 request_id、actor_id、project_id 关联业务。登录事件允许 project_id 为空，未知登录身份可令 actor_id 为空。

不得新增第二套用户表。密码摘要验证、账户禁用和持久化会话尚未在本阶段实现。

## 内部类型与输入规则

- 标识符均为 UUID 对象，时间为带时区 datetime，HTTP 序列化才转换为字符串。上下文 Context(user_id, project_id, request_id) 全部必填，只能由可信服务端构造。
- FileReference(storage_key: str, paper_id: UUID, project_id: UUID) 全部必填；storage_key 是相对存储键，不是客户端提供的绝对路径或任意 URL。A 仅保存引用，不自行打开客户端路径。B 负责文件存在性、大小和实际 SHA-256；A 校验引用项目以及摘要格式。
- TaskKind = process / answer / evidence / draft。
- TaskPayload 的实际值仍为 dict；process 必须包含 file、paper_ids=[file.paper_id]，不能包含 question；其他类型必须包含 QuestionRequest 和显式 paper_ids 快照，不能包含 file。evidence 恰好一个论文；answer/draft 可为空集合以表示证据不足。
- QuestionRequest.paper_ids 非空时必须与载荷范围一致；空值表示 B 已解析范围。集合去重、按 UUID 排序；字典键顺序不影响幂等指纹。未知字段拒绝，不能把 JSON 字符串或 repr 当稳定序列化。
- JobResult 为 ProcessResult / AnswerResult / EvidenceResult / DraftResult，与任务 kind 一致。Job 是领取时的分离快照；task.attempts 是本次执行的比较条件，不可由前端上传。
- key 必填、1—128 字符，只允许字母数字、连字符和下划线；业务入口未提供时由 B 生成新键。
- 所有返回对象均与适配器状态分离；调用方改动返回对象不得改写持久化数据。

## 26 个方法的逐项约定

下表输入除带 `=...` 者外均必填。所有方法归属 A；B 的认证服务、路由或 worker 是可信调用方。每行的请求/返回实例在 JSON 的同名方法条目中。权限规则 P/U/W/A、事务规则 T、重试规则 R 在表后完整定义。

测试锚点指 `backend/tests/data_contracts/test_data_port.py` 中 `test_` 之后的名称，省略号表示同名前缀，方括号表示参数化场景；真实门槛位于 test_real_database.py。

| 方法与输入 | 返回 | 不存在/唯一性及错误 | 权限、事务、重试 | 共享测试锚点 |
|---|---|---|---|---|
| user_by_name(username: str) | User 或 None | 不存在 None；用户名唯一由初始化/用户管理约束 | U；只读；R-read | user_and_membership_absence |
| user_by_id(user_id: UUID) | User 或 None | 不存在 None | U；只读；R-read | user_and_membership_absence、returned_objects_are_detached |
| role(user_id: UUID, project_id: UUID) | owner/member 或 None | 用户、项目或关系不存在均 None；成员关系唯一 | U；只读，不缓存权限；R-read | user_and_membership_absence、member_add_repeat_remove_and_roles |
| projects(user_id: UUID) | list[Project]，附当前角色 | 用户不存在 404；无项目 [] | U；只返回该用户参与项目；R-read | create_project_and_owner_relationship_atomic |
| create_project(user_id: UUID, data: ProjectCreate, request_id: UUID) | Project(role=owner) | 用户不存在 404；项目名称不唯一，不以 request_id 去重 | U；T-project；R-new | create_project_and_owner_relationship_atomic、commit_failure…[create_project] |
| members(ctx: Context) | list[Member] | 项目不可访问 404；普通成员 403 | P-owner；只读；R-read | member_add_repeat_remove_and_roles |
| add_member(ctx, username: str) | Member | 用户不存在 USER_NOT_FOUND；重复添加返回原角色，不重复行 | P-owner；T-member；R-repeat | member_add_repeat_remove_and_roles |
| remove_member(ctx, user_id: UUID) | None | 目标关系不存在成功空操作；移除负责人 OWNER_IMMUTABLE | P-owner；T-member；R-repeat | member_add_repeat_remove_and_roles |
| papers(ctx, year_from: int或None=None, year_to: int或None=None) | list[Paper] | 无结果 []；年份 1000—3000，范围倒置 422 | P-member；只读，排除移出论文；R-read | paper_filters_and_missing_resources |
| paper(ctx, paper_id: UUID) | Paper | 不存在、移出、跨项目均 404 | P-member；只读；R-read | member_of_both_projects_cannot_substitute_resource_ids[paper] |
| sources(ctx, paper_id: UUID) | list[Source] | 论文不可访问 404；尚无片段 [] | P-member；只读；R-read | sources_evidence_and_missing_evidence_statistics |
| evidence(ctx, paper_id: UUID) | list[EvidenceResult] | 论文不可访问 404；未提取 [] | P-member；只读；R-read | sources_evidence_and_missing_evidence_statistics |
| remove_paper(ctx, paper_id: UUID) | None | 不存在/已移出 404；仅逻辑移出，不物理清理历史来源 | P-owner；T-remove；R-read-before-retry | removal_hides_live_resources_preserves_authorized_snapshot |
| import_paper(ctx, title: str, year: int或None, digest: str, file: FileReference, key: str) | tuple[Paper, Task] | title 1—200 字符；digest 为 64 位小写 SHA-256；重复项目摘要 DUPLICATE_PAPER；编号占用 IDENTIFIER_CONFLICT；同键异参 IDEMPOTENCY_CONFLICT | P-member；T-import；R-key | import_atomic_metadata_task_and_duplicate、file_reference_scope_and_identifier_conflict、commit_failure…[import_paper] |
| create_task(ctx, kind: TaskKind, payload: TaskPayload, key: str) | Task(pending) | 范围中论文不可访问 404；载荷非法 DATA_CONTRACT_ERROR；同键异参 409 | P-member；T-task；R-key | task_payload_canonicalization_and_replay、invalid_task_payload_rejected_without_task |
| task(ctx, task_id: UUID) | Task | 不存在、跨项目或引用已移出论文均 404 | P-member；只读；R-read | paper_filters_and_missing_resources、member_of_both_projects…[task] |
| retry_task(ctx, task_id: UUID) | Task(pending) | 只允许 failed；其余 TASK_NOT_RETRYABLE；论文不可访问 404 | P-member；T-retry；R-state | retry_fences_old_attempt_and_completion_is_idempotent |
| claim_job() | Job 或 None | 无 pending 返回 None；不得因无任务报错 | W；T-claim；R-read-after-ambiguity | task_payload_canonicalization_and_replay；真实 test_concurrent_workers_claim_once |
| complete_job(job: Job, result: JobResult) | None | 任务缺失/伪造项目 404；旧 attempts、状态或不同重放结果 TASK_STATE_CONFLICT；引用非法 INVALID_CITATION；结果不匹配 ADAPTER_CONTRACT_ERROR | W + 完成时重验成员/论文；T-complete；R-attempt | retry_fences…、processing_result_must_match…、commit_failure…[complete_job] |
| fail_job(job: Job, code: str, message: str) | None | 任务缺失 404；旧 attempts 或已成功 TASK_STATE_CONFLICT；同次相同失败重放为空操作 | W；允许可信 worker 关闭被撤权用户的在途尝试，不写入研究结果；T-fail；R-attempt | complete_rechecks_revoked_membership、forged_job_context_does_not_overwrite |
| save_report(ctx, data: ReportSave, key: str) | ReportVersion | 新建 report_id/base_version_id 都空；更新时 report_id 存在且基础版本最新，否则 404/VERSION_CONFLICT；来源无效拒绝 | P-member；T-report；R-key | report_versions_snapshot_idempotency_and_optimistic_conflict、report_snapshot_revalidated_inside_data_boundary、commit_failure…[save_report] |
| versions(ctx, report_id: UUID) | list[ReportVersion]，number 升序 | 不存在/跨项目 404；现有报告至少一个版本 | P-member；只读，允许读取授权历史快照；R-read | report_versions_snapshot_idempotency_and_optimistic_conflict |
| reports(ctx) | list[ReportVersion]，每报告最新版本 | 项目不可访问 404；无报告 [] | P-member；只读；R-read | report_versions_snapshot_idempotency_and_optimistic_conflict |
| controlled_statistics(ctx, intent: StatisticsIntent) | tuple[list[str], list[dict]] | 仅 by_year/by_topic/missing_evidence；其他 UNSUPPORTED_INTENT；不是 SQL 输入接口 | P-member + 仅 D 网关/可信内部统计调用；只读；R-read | sources_evidence_and_missing_evidence_statistics、non_member_cannot_read_or_write |
| append_audit(event: AuditEvent) | None | 同 event_id 同内容空操作；不同内容 AUDIT_EVENT_CONFLICT；非空项目/actor 不存在 404 | A-trusted；T-audit；R-event | audit_unique_event_pagination_filters_and_scope、commit_failure…[append_audit] |
| audit(ctx, *, action: str或None, decision: str或None, actor_id: UUID或None, since: datetime或None, until: datetime或None, offset: int, limit: int) | AuditList | 所有关键字参数必填，过滤不限传 None；无结果 items=[]/total=0；offset≥0，1≤limit≤100，时间带时区且顺序正确 | P-owner；只读；R-read | audit_unique_event_pagination_filters_and_scope |

P-member：B 先认证并查询最新成员关系；A 在每次资源访问/写入时再次校验成员与资源项目，不能只相信预检。P-owner 在此基础上要求 owner。U 是仅供可信认证/项目入口的全局查询，不提供给浏览器任意查用户。W 是仅供后台 worker 的内部方法，Job 必须对应先前领取快照；无 HTTP 暴露。A-trusted 仅允许后端/D 追加审计，不接受客户端提交的审计主体。

不存在与越权统一 RESOURCE_NOT_FOUND（404），不暴露标题或记录是否存在；OWNER_REQUIRED 为 403。A 的原生数据库角色/RLS 与 D 的策略是额外要求，不因 DataPort 校验而免除。

Papers/projects/reports 列表不承诺业务排序或分页；未来分页应另行兼容扩展。audit 按 timestamp、event_id 稳定倒序，total 为同一筛选范围总数；offset 在并发插入时不保证跨页快照一致，若需要游标另行升级。

## 事务边界

每个变更方法是独立提交/回滚单元；业务服务、文件系统和 AI 调用不共用一个长事务。

DataPort 保持同步方法约定。真实 A 每次调用应建立独立事务/连接作用域，不向 B 返回懒加载 ORM 对象或跨线程 Session。B 在真实集成时需对 async 上传、检索与 worker 路径中的同步数据库 I/O 做线程池隔离；当前内存实现不能证明真实 I/O 对事件循环的影响。

| 事务 | 必须一起提交的内容 |
|---|---|
| T-project | 项目 + 创建者 owner 成员关系 |
| T-member | 成员唯一性、角色与关系变更；移除后下一请求重新授权 |
| T-import | 论文导入元数据 + 项目关联 + 摘要/编号唯一检查 + pending 任务及载荷 + 幂等记录 |
| T-task | 范围检查 + pending 任务及规范化载荷 + 幂等记录 |
| T-retry | failed 条件更新为 pending + 清空旧错误/结果 + 新 Context/request_id + 处理论文恢复 pending；不创建第二条任务 |
| T-claim | 只领取一次合格任务 + processing + attempts 增加 + 论文处理状态更新。SQL/锁的选择归 A |
| T-complete | 对 task_id/attempts/可信 Context/processing 做条件检查 + 结果来源校验 + 片段/证据或回答结果 + searchable 等派生状态 + succeeded 和 result_ref |
| T-fail | 同次 processing 条件检查 + 错误/更新时间 + failed；清空未成功结果；处理论文同步 failed |
| T-report | 最新基础版本检查 + 正文/问题 + number/version_id + 完整引用快照 + 幂等记录 |
| T-remove | 项目论文可见性变更；保留历史来源快照；禁止新检索和普通任务读取 |
| T-audit | event_id 去重与追加；不提供更新或删除接口 |

A 的真实事务应同时持久化上述变更的最小领域审计记录，或同库 outbox 事件，包含 request_id、actor、project、动作和最终状态。D 可以消费 outbox 汇入审计；这是**真实模式上线门槛**，当前 Mock 没有持久化 outbox，相关数据库验证尚未完成。无需为此新增 DataPort 的大事务参数。

B 的 append_audit 调用是独立事务，不能与前一个业务方法假定原子。requested/pending 表示尝试；completed/saved 等成功事件只能在方法返回后记录。后续审计失败不能把已提交任务改为 failed，不能声称业务已回滚。HTTP 503 也可能发生在业务提交后；有幂等键时原键重放，无幂等键时先查当前状态。request_id 只用于关联，不是幂等键。

动态 SQL 仍由 D 自己保证执行前关键审计，审计失败就不执行；B 的事后日志和普通业务 outbox 不能替代该规则。

### 文件与外部 AI

B 在调用 import_paper 前保存文件并算摘要，A 保存摘要及 FileReference。文件写入不在数据库事务中。明确的数据库回滚或重复导入时，B 只清理本次创建且未被采纳的文件；连接在提交时中断导致的未知提交状态不能直接删文件。

上传路由现已区分明确回滚与 DATA_COMMIT_UNKNOWN：未知提交以及无法分类的内部异常保留本次文件，返回待确认错误；相同幂等键重放后只清理新的未采纳文件。已用模拟“提交成功但确认丢失”验证文件保留和重放。真实数据库断连及对象存储回收仍须集成验收，不能把此 Mock 测试当真实提交故障证据。

C 调用在 T-claim 提交之后、T-complete 开始之前；网络等待期间不得持有业务事务锁。调用失败可以写 T-fail，之后安全重试。AI 外部调用可能重复，不能承诺端到端 exactly-once；数据结果提交通过 task_id + attempts 和唯一约束避免重复。进程崩溃后的租约过期与恢复机制由 A/B 实现，当前 Mock 不具备。

当前 ProcessResult 仅能提交来源视图。完整正文、嵌入、索引完成标志需 A/C 冻结补充载荷与原子提交方案；未经真实索引完成不能把生产论文标成 searchable。

## 唯一性与幂等

- username 唯一；(project_id,user_id) 唯一；项目 owner 关系与项目记录一致。
- 公开 paper_id/chunk_id/evidence_id/task_id/report_id/version_id/event_id 唯一；项目关联与资源项目一致。
- (project_id,digest) 唯一。AB-1 延续移出后摘要仍占用的行为，重新上传返回 DUPLICATE_PAPER；若产品要支持恢复，需 A/B 明确恢复语义，不得悄悄新建或覆盖历史来源。
- (report_id,number) 唯一；更新报告使用 base_version_id 比较并发版本。两个并发保存只能有一个成功创建下个版本，另一个 VERSION_CONFLICT。
- 幂等键作用域为 user_id + project_id + operation + key。规范化请求和业务实体在同一事务保存；字典键顺序不影响指纹。重放返回原实体/结果快照，当前状态可另查。
- 上传键比对 title/year/digest，第二次尝试的新暂存文件键和随机 paper_id 不参与外部请求指纹；返回首次采纳的 paper_id/task_id。不得留下新的悬空任务。
- 幂等重放不跳过当前授权。论文移出后，旧上传键不能作为读取论文/任务的缓存后门（404）；新键仍按摘要唯一性返回 DUPLICATE_PAPER。已保存报告的历史快照仍按项目权限读取。
- 同次 complete_job 同结果空操作；不同结果或迟到 attempts 返回 TASK_STATE_CONFLICT。fail_job 同次同错误空操作；旧 worker 不能把新状态改为失败。
- retry_task 只有 failed→pending；重复发起重试可能返回 TASK_NOT_RETRYABLE，但始终只有原 task_id。这是状态保护，不是无条件成功重放。
- append_audit 同 event_id 同内容空操作，不同内容返回 AUDIT_EVENT_CONFLICT。request_id 相同可有多个不同事件。

## 错误与重试

适配器将数据库异常映射为 AppError(status, code, safe_message)，不暴露驱动类型、约束名、SQL、连接字符串、凭据或正文。

| 错误 | HTTP | 重试规则 |
|---|---|---|
| RESOURCE_NOT_FOUND / USER_NOT_FOUND | 404 | 不自动重试；先修复范围/输入 |
| OWNER_REQUIRED | 403 | 不重试；需要负责人 |
| DATA_CONTRACT_ERROR | 422 | 参数或载荷不合法，不重试 |
| DUPLICATE_PAPER / IDENTIFIER_CONFLICT / OWNER_IMMUTABLE / UNSUPPORTED_INTENT | 409（意图不支持为400） | 不自动重试同一操作 |
| IDEMPOTENCY_CONFLICT / VERSION_CONFLICT / TASK_NOT_RETRYABLE / TASK_STATE_CONFLICT / AUDIT_EVENT_CONFLICT | 409 | 读取当前状态，修正请求或放弃迟到结果，不盲目重试 |
| INVALID_CITATION / ADAPTER_CONTRACT_ERROR | 502 | 检查 C 输出或来源；不要反复提交无效结果 |
| DATA_UNAVAILABLE | 503 | 仅在已知事务回滚后使用；允许有限重试，保留原幂等键 |
| DATA_COMMIT_UNKNOWN | 503 | 提交结果未知；仅可用幂等键对账/重放，禁止新建重复操作或立即删暂存文件 |

R-read：只读可有限重试。R-key：相同键相同参数重试。R-event：相同 event_id/内容重试。R-attempt：只能重放同次相同结果/错误，状态变更后停止。R-state：重查任务状态，不重复创建。R-new：create_project 无幂等键，提交未知时必须先对账；不把 request_id 当唯一约束。R-read-after-ambiguity：领取结果未知时不能猜测持有 Job，等待 A 的租约恢复。所有重试仍重新授权。

## 共享测试与真实适配器接入

```powershell
# 当前：只使用 Mock，5 个真实数据库门槛自动 skip 并显示原因
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts -q -rs

# A 提供专用隔离测试库 fixture 后才运行；不复用生产配置
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider module_name:build_harness -q -rs
```

provider 必须是 context manager factory，签名 `build_harness(*, tmp_path)`，yield `DataHarness`。在 provider 中使用唯一导入路径 `from data_contracts.harness import DataHarness`，pytest 已把 backend/tests 放入测试导入路径；不要另用命名空间路径重复加载该类。根 conftest 已注册 CLI 参数。

Harness 提供 data、owner/member/outsider（已初始化 User）、kind="database" 和 failing_commit(operation)。failing_commit 必须在指定操作写入后、提交前制造可确认回滚的故障，映射 DATA_UNAVAILABLE；不能只在调用前抛错伪造事务测试。测试代码只使用 DataPort 行为，不查看 Mock 字典或数据库表。Mock 故障探针仅供测试，不属于生产 DataPort。

真实门槛还要求 reopen() 返回重连适配器、expire_claim(job) 在隔离环境触发真实租约恢复。测试涵盖重连持久化、并发同摘要导入、并发报告保存、并发领取、旧租约写入隔离。缺少必需能力会失败，不能用 Mock 冒充通过。A/D 的原生数据库角色和 RLS 攻防需另外验证，不由 Mock 覆盖。

## A 实现后的验收清单

- 空测试库可迁移和安全初始化；三名测试用户使用同一用户模型；重复初始化不破坏业务数据。
- 全部共享测试通过，5 个真实数据库门槛不再 skip；测试库与生产隔离。
- 提供真实 SQL 约束/事务故障证据，不能用进程锁代替数据库并发保证。
- 同项目重复导入不留多余论文/任务；跨项目同文件独立授权；成员撤销立即生效。
- 重连/重启后论文、任务、证据、报告及引用快照存在；任务租约恢复能拒绝旧 worker。
- 正文、片段和向量索引提交边界与 C 一致；报告来源在数据层重新授权。
- 与 D 完成受限数据库身份、视图、RLS 和审计防篡改；没有通用 SQL 执行后门。
- 领域事件/outbox 与业务事务一起提交，审计投递可重试；未知提交结果有对账和文件补偿。
- B 再注册真实工厂分支与配置，验证 readiness 和部署。当前 real 模式继续拒绝启动。

## 需团队确认的未决事项

1. A/B/D：持久化会话、密码摘要验证接口、账户禁用以及连接池中的项目上下文复位方式。
2. A/C：完整正文/向量存储载荷、嵌入维度和模型版本、索引完成事务；当前 Source 不能承载全部索引内容。
3. A/C：作者和主题的写入结构，按主题统计是否一篇多主题分别计数。
4. A/B：文件存储实现、未知提交对账、孤立文件回收；移出后是否允许恢复/再次导入。
5. A/B：租约时长、心跳、超时恢复与最大重试次数；attempts 单调比较规则已冻结。
6. A/D：事务内审计或 outbox 的具体结构、投递确认和保留周期，以及原生数据库权限验收。
7. A/B：数据库 readiness 与初始化命令、迁移所有权；不在本阶段扩充 DataPort 生命周期方法。

这些事项不阻碍 A 按 26 方法实现基础关系、事务、约束和共享测试，但涉及真实认证、检索、SQL 执行与可靠恢复的上线能力必须在启用前完成相应确认。
## Phase 2B 实现状态说明

以上 AB-1 契约正文与 `ports.py` 的 26 个签名不变。新增标准库 SQLite 开发适配器及共享测试 provider，配置、事务实现和限制见 [基础持久化](persistence.md)。本文中“等待 A/真实数据库”的描述是冻结时的正式数据库交接要求；SQLite 已验证的本机场景见 [验证记录](verification.md)，不替代 A 的正式数据库验收。

## Phase 2B 数据库中立调整（当前）

上一节 SQLite 说明为历史实验，已解除默认工厂绑定，未新增或修改模型与迁移。正式数据库及实现由 A/团队确认。新增的 DataBinding 只承载既有 DataPort 与独立 readiness/close，不加入、不替换、不改变 AB-1 的 26 个方法。persistent/real 未注册即拒绝启动。当前接入示例、41 项共享测试中的 6 个数据库门槛，以及未知提交的文件处理边界见 [中立说明](persistence-integration.md)。

