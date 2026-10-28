# 成员 D 当前进度与下一阶段顺序

更新日期：2026-10-28。D 的任务 1～9 已有本地实现与固定参考组合验证；正式部署事项另列于[验证记录](member-d-security-validation.md)。不表示 A/C 分支已合入或团队已批准上线。

## 历史 PR 快照（来自前轮查看，本轮未刷新远程状态）

| 成员 / PR | 本次查看的版本与状态 | 对 D 的实际影响 |
|---|---|---|
| [A / #1](https://github.com/SiriThree/PaperPilot/pull/1) | 执行前复核为 `member-a@0b2953c`，Open / Draft | 已提供 PostgreSQL 16、psycopg3、24 表迁移、26 方法 DataPort、DataBinding、隔离库 provider 和 outbox 投递器；另有性能/备份恢复记录。D 已基于此验证双视图和项目登录边界；正式角色生命周期、业务库部署及 outbox 保留/死信仍待联合完成。 |
| [B / #3](https://github.com/SiriThree/PaperPilot/pull/3) | 已合并到 `main@4770732`；本轮已同步 D 分支并回归 | 业务反馈、来源校验和查询/审计页面已更新，页面明确区分 allowed 与 executed；D 无需另做前端。 |
| [B / #5](https://github.com/SiriThree/PaperPilot/pull/5) | 已合并到 `main@177dddc`；本轮已同步 D 分支并回归 | 新增研究流程衔接、Mock 安全证据展示与报告测试；不代表真实 SQL/数据库隔离已验收。 |
| [C / #4](https://github.com/SiriThree/PaperPilot/pull/4) | `feat/member-c-ai@1f43093`，Open / 非 Draft，未合并 | 已提供五方法真实 AI、PDF reader、检索和受控生成；`app.ai.bootstrap.create_app` 接受 `data_binding` 与 `security_factory`。可用于最终组合，当前没有 NL2SQL 方法，D 的三条固定统计不以 C 的模型或向量索引为前置。 |
| [D / #2](https://github.com/SiriThree/PaperPilot/pull/2) | `feature/member-d-security@2f781e0`，Open / Draft | 任务 1/2 与任务 3 校验阶段已交付。真实 SQL 始终未执行，受限执行器、关键审计、真实 D provider 及正式攻防尚未完成。 |

来源固定在本次查看的提交：[A 数据层记录](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/docs/data-adapter.md)、[A 正式迁移](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/backend/app/data/migrations/001_initial.sql)、[A 统计与审计实现](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/backend/app/data/adapter.py)、[C 接入说明](https://github.com/SiriThree/PaperPilot/blob/1f430936acf23c598a91ce794d70ca8c536d39e4/docs/ai-adapter.md)、[C 组合入口](https://github.com/SiriThree/PaperPilot/blob/1f430936acf23c598a91ce794d70ca8c536d39e4/backend/app/ai/bootstrap.py)。

A 的 41 项真实库共享测试、9 项业务联调及 5 项 outbox 测试，C 的 56 项真实适配器/替身契约测试均为各 PR 的交付记录，本轮没有在 D 环境复跑，不能替代 A/D 安全验收。C 的真实模型也不代表已提供 SQL 生成能力。

## D 任务状态

| 原任务 | 当前状态 | 调整后的剩余内容 |
|---|---|---|
| 1．SecurityPort 与构造入口 | 已完成 | 保留两方法契约；显式项目 ReaderConfig 注入已在任务 9 验证。 |
| 2．应用层授权策略 | 已完成，已接 A 回归 | 已验证 A 的 owner/member、移除成员后的即时拒绝；数据库隔离另有独立验证，不以应用授权代替数据库权限。 |
| 3．自然语言统计网关 | 已完成 | 三棵 PostgreSQL AST、psycopg 命名参数和双视图已对照 A 验证；显式配置读取身份后可真实执行。 |
| 4．受限数据库执行器 | 本地实现和真实库验证已完成 | 独立连接、身份/权限检查、只读事务、3 秒数据库/应用预算、100 行、256 KiB、取消并等待清理。正式库角色部署另由 A/D 落实。 |
| 5．执行结果与异常映射 | 已完成 | JSON 类型/UUID 转换、成功、连接不可用、执行故障、超时/取消、结果超限及错误脱敏已有验证。 |
| 6．关键安全审计 | 门槛及独立最小权限写身份已完成隔离验证 | 复用 A append_audit/event_id，提交前检查身份权限；禁止专用登录修改/删除审计或读取业务内容。见[身份说明](member-d-identities.md)。正式安装和 outbox 保留/死信仍待联合部署。 |
| 7．隔离 SecurityHarness provider | 已完成 | security_contracts.postgres_provider:build_harness 的 8 项真实共享契约通过；故障来自真实锁超时/错误凭据，不使用 Mock 伪造成功。 |
| 8．真实安全测试 | 本地隔离攻防已完成 | 44 攻击 + 21 正常、44 项直接绕过，以及权限变化、审计、限时限量、断连/取消/清理和并发隔离；见[验证记录](member-d-security-validation.md)。 |
| 9．团队交付与联调 | 固定参考本地组合与可信启动入口已完成 | bootstrap、严格 JSON/秘密引用配置及显式 A/C 工作树加载入口已验证。见[启动说明](member-d-startup.md)。未验收外部模型、向量索引、浏览器或生产认证。 |

## 下一步顺序与完成门槛

1. **同步 B 的已合并基线并复验：已完成。** 本轮从 origin/main 合入 `4770732`，无文本冲突；Python 3.12 默认后端 371 passed / 37 skipped，前端类型及构建、公开契约和静态检查通过。A/C/D 最终合入时仍须同时保留 D 的 sqlglot、A 的数据层依赖及 C 的 pypdf/可选 AI 配置，不以覆盖整个 pyproject/锁文件处理差异。
2. **依据 A 实现接入约定和任务 3 的正式模板：D 侧已完成。** 用户已明确要求实施，详见 [A/D 接入约定](ad-security-contract-proposal.md)。双视图、PostgreSQL/psycopg 参数、独立项目登录及撤销边界已在 A 固定版本真实迁移上验证；没有 A 的人工评论不再阻塞独立实现。正式业务库安装与角色生命周期仍在后续联合部署范围，未启用应用执行。
3. **任务 6 的审计失败关闭：D 门槛已完成。** 已用可控执行依赖和 A 固定版本真实 PostgreSQL 验证执行前/后确认、回滚、提交后确认丢失及 event_id 幂等。默认入口仍无 SQL 执行器；正式审计身份和部署不计作完成，见[任务 6](member-d-audit.md)。
4. **任务 4/5/7：已完成。** [执行器说明](member-d-executor.md)包含可信项目身份注入、完整结果/错误映射、资源清理及真实 Provider；默认不配置仍保持 not_executed。
5. **任务 8/9：本地验证已完成。** 攻防和固定 A/C 参考组合见[验证记录](member-d-security-validation.md)。正式库安装、角色/审计身份、生产认证、模型服务及实际合并后的最终团队验收仍待各负责人落实。

## 统计语义与本轮处理

- A 的 `papers.metadata_id` 是文献元数据身份，公开 paper_id 来自 `project_papers.paper_id`；统计必须通过项目内关系连接，排除 `project_papers.removed_at IS NOT NULL`。
- A 用 `topics + paper_topics` 表达多对多主题，按主题统计为每主题 `COUNT(DISTINCT pp.paper_id)`。D 已拆分独立论文/主题视图，并同步三棵固定 AST；多主题不再导致年份 COUNT(*) 或未提取列表重复。旧的单视图模板被拒绝，没有开放任意 SQL 范围。
- A 当前 DataPort 没有主题写入方法，C 的 ProcessResult 也不提交主题标签。需明确主题数据来源；尚无主题时允许真实空结果，不能填入 Mock 主题来伪装真实统计。
- A 的未提取统计用同项目、同论文 `task_results.kind='evidence'` 的存在性判断。D 的 has_evidence 应对齐此语义；C 返回不足且已保存证据记录，不等于“尚未提取”，也不代表研究结论得到验证。
- A 正式迁移已有 `audit_events` 和 `outbox_events`，D 在独立 SQL 资源中新增双视图和受保护登录绑定，不修改 A 的原迁移。实际项目隔离由 security_barrier 视图及 SESSION_USER 绑定实施，不声称已改造 A 底表原生 RLS。SQL 读取身份和审计写入权限仍须分离。

## 进度复核时的范围与验证口径（历史）

本次只读查看 PR 信息、选定源码和本地已缓存的 origin/main，并更新 D 的进度与交接文档；未合并、修改或执行 A/B/C 的代码，未改公开接口、前端或执行开关，未发布 GitHub 评论/评审或修改 PR。

D 的 Python 3.12.10 基线验证仍为 **344 passed / 37 skipped / 0 failed**（见[网关验证记录](member-d-gateway.md)），属于当时 D 分支，不能声称覆盖 B 新基线或 A/C 的联合运行。PR #2 的在线正文仍使用较早的 3.14/分组测试口径，后续应同步该基线复验记录；本轮未修改远程正文。

真实 SQL 保持关闭，执行前关键审计、正式数据库权限与隔离 provider 尚待实现；此次进度调整不能计为这些任务完成。

## 2026-10-28：按计划执行——已同步 B 基线

用户授权按顺序推进后，先单独提交此前进度文档，再从 origin/main 合并 B 的 `4770732`，保留 D 的全部功能提交与依赖；无文本冲突，未合并尚未进入 main 的 A/C 分支。

- Python 3.12.10 默认后端：371 passed、37 skipped、0 failed；跳过仍为 31 项历史 SQLite 实验与 6 项真实数据库门槛，未计作通过。报告位于 Git 忽略的 `.runtime/d-main-sync-tests.xml`。
- Node 24.15.0，按 frontend/package-lock.json 执行 npm ci；前端 typecheck 与 Vite build 通过，保留既有大包提示。未调整 npm 的脚本许可配置，未运行浏览器 E2E。
- OpenAPI、C/D Mock 示例一致性、Ruff 检查及 68 个文件格式检查通过。保留既有 Starlette/httpx TestClient 弃用提示。
- 执行前已重读 A 最新 `0b2953c`：数据库受限角色、RLS 项目上下文及正式视图仍是 A/D 协作项，不能标记为已确认。
- 本机 Docker 引擎当前不可达，且没有 `.runtime/day0.env`；尚未建立或修改真实数据库，也未启用 SQL 执行。

下一步的具体协作前置已整理到 [A/D 接入草案](ad-security-contract-proposal.md)。本轮已完成第 1 步并提交，通过第 2 步源码核对和方案准备；尚未得到角色、项目绑定和视图的正式确认，不冒充完成后续执行器/真实库验证。

## 2026-10-28：继续同步 B PR #5

从 origin/main 合入已合并的 `177dddc`，无文本冲突，保留 D 的实现。Python 3.12.10 默认后端 **374 passed / 37 skipped / 0 failed**，前端 typecheck/build、Ruff 与 OpenAPI/C/D 示例检查通过；报告为 `.runtime/d-b5-sync-tests.xml`。保留既有 Starlette/httpx 与 Vite 大包提示，未运行浏览器 E2E。

B 的安全证据报告范围仍为隔离 Mock 应用，D 的应用 SQL 继续 not_executed。第二步已按用户要求完成，见下节；没有把 A 尚未给出的正式部署审批写成已获批准。

## 2026-10-28：第二步已完成

已依据 A 的 `0b2953c` 正式迁移实现独立双视图 SQL 资源，将三条模板切换为 PostgreSQL 和 psycopg 命名参数。多主题、年份空值、移出论文和证据存在性的结果均与 A 的 DataPort 对照一致。两个 security_barrier 视图按受保护的 SESSION_USER 项目绑定过滤；最小权限登录不能直读底表/审计/outbox、写入或切换身份，伪造项目参数/GUC 不越界，撤销绑定对已有连接也生效。A 的真实成员授权和移除回归通过。

Docker Desktop 已启动；验证仅创建并自动删除独立临时 PostgreSQL 16 容器，未连接业务库。A 的固定参考位于 Git 忽略的 `.runtime/member-a-step2-reference`，未合并 A 分支；新增测试依赖单列在 `requirements-security-test.lock`。数据库登录凭据仅在测试内存中生成，不写入仓库或 env 文件。

Python 3.12.10 全量后端 **401 passed / 37 skipped / 0 failed**，含 22 项真实 PostgreSQL 契约测试。37 项跳过仍为历史 SQLite 31 项及默认 Mock 数据层真实库门槛 6 项，未计作通过。报告 `.runtime/d-step2-combined-results.xml` 记录 PostgreSQL 版本、A 提交和镜像摘要。公开契约及 C/D 示例检查、Ruff 和依赖检查通过；前端沿用 B #5 同步时已通过的 typecheck/build，本步无前端改动。

下一步为任务 6：执行前后关键审计及失败关闭，再完成执行器/结果映射和共享 SecurityHarness。本步真实 SQL 只在隔离测试里执行；应用仍返回 allowed/not_executed，没有启用执行器或宣称完成原生 RLS、40+20 攻防及正式部署验收。复现命令和权限边界见 [A/D 接入约定](ad-security-contract-proposal.md)。

## 2026-10-28：第三步关键审计门槛已完成

复用 A append_audit，新增 security.query.prepared/finished 关键事件，执行前确认失败不调用执行依赖，执行后确认失败不返回行；拒绝、默认关闭、故障和取消均记录脱敏状态。事件 ID 与请求关联分离，沿用 A 同事件幂等；不记录问题、候选 SQL、结果内容或凭据。实现与限制见 [任务 6](member-d-audit.md)。

Python 3.12.10 全量 **430 passed / 37 skipped / 0 failed**，含 27 项真实 PostgreSQL 测试；跳过口径不变。新增 24 项可控依赖/HTTP 测试及 5 项 A/D 真实审计提交、回滚、确认丢失模拟、持久化和重放测试。报告为 `.runtime/d-step3-combined-results.xml`；Ruff、72 个文件格式、OpenAPI、C/D Mock 示例、依赖及差异检查通过。一次性容器已清理，保留既有 Starlette/httpx 弃用提示。

默认入口仍无 SQL 执行器，允许的查询继续 allowed/not_executed。下一步为任务 4/5/7：受限执行器、结果映射及隔离 SecurityHarness。尚未验收数据库取消清理、3 秒/100 行/256 KiB 限制、40+20 攻防、正式审计写身份和业务库部署。本轮未运行前端/浏览器测试，未推送或修改远程 PR。

## 2026-10-28：剩余开发任务及固定参考联调已完成

已实现并验证任务 4/5/7：PostgresQueryExecutor、可信项目身份注入、每次新建并关闭的只读连接、权限漂移检查、参数化计划复核、3 秒 SQL/应用预算、100 行、256 KiB、取消清理和完整结果/错误映射。补充完成审计后返回前的成员复查，避免审计期间撤权仍返回行。真实 SecurityHarness 8 项通过，详见[执行器说明](member-d-executor.md)。

任务 8 已完成实际配置链路 44 攻击 + 21 正常（两个项目、owner/member）、44 项直接绕过及资源/审计/隔离验证。任务 9 依次完成 A+D/C=Mock 和 A+C RealAI+D 的 HTTP 流程；C 模型 HTTP 端使用本地可控替身，未调用外部模型或下载权重，未补建持久化向量索引。

最终 Python 3.12.10 全量 **594 passed / 37 skipped / 0 failed**，含 174 项实际 PostgreSQL 测试；真实共享 Provider 8 项另行通过，不重复相加。Ruff、77 个文件格式、公开契约、Mock 示例、依赖及 wheel 资源检查通过；报告 `.runtime/d-remaining-combined-results.xml` 与 `.runtime/d-executor-harness.xml`。测试容器已自动清理，未推送或修改远程 PR。

尚需联合部署/验收：正式库视图安装和所有者、项目读取角色/凭据生命周期、最小权限审计写身份与防篡改、outbox 保留/死信、生产认证，以及实际合并版本的最终团队验收。默认工厂不配置 readers 继续禁用执行；显式可信配置后已能真实执行。本轮没有前端改动，未运行浏览器 E2E。完整范围和复现见[验证记录](member-d-security-validation.md)。

## 2026-10-28：按用户限定，仅补齐前三项

1. **独立审计身份**：compose_audit 复用 A append_audit/event_id，独立连接池、最小表/列权限、启动及提交前 ACL 检查；专用登录不能修改/删除审计或读取业务内容。D 的两阶段关键事件不再需要业务管理员审计连接。B 普通日志保持原业务 DataPort；不声称防御 DBA 或其他管理员身份。
2. **读取身份生命周期**：manage_security.py 显式安装 D 资源/登记表，创建项目读取及审计登录、轮换密码、撤销权限/绑定并终止旧会话。角色名+OID 登记校验避免误改已有账号；不保存/输出密码，不自动重试未知提交，不删除业务数据。见[身份与管理说明](member-d-identities.md)。
3. **实际启动入口**：app.security.bootstrap:create_app + 严格 JSON/环境秘密引用配置；run_security.py 可明确加载已审阅 A/C 工作树，支持 A+D、A+C+D。错误配置/身份拒绝启动并清理资源，默认生产拒绝规则不变。见[启动说明](member-d-startup.md)。

第一阶段本地提交为 `cd7faf7`。最终 Python 3.12.10 全量 **645 passed / 37 skipped / 0 failed**，其中实际 PostgreSQL 211 项；真实共享 SecurityHarness 另行 8 项通过，不重复累加。新增 50 项定向测试与读取转授权回归已包括在全量中。Ruff、86 文件格式、OpenAPI、C/D Mock 示例、依赖及 wheel 新模块/SQL 资源验证通过。报告 `.runtime/d-operations-combined-results.xml`、`.runtime/d-operations-harness.xml`。

本轮严格不扩展到浏览器验收、正式库部署、生产认证、outbox 策略或真实模型效果；未推送或修改远程 PR。正式库实际安装/角色分发仍由团队部署流程执行；本轮管理操作仅在自动清理的独立测试库完成。
