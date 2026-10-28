# 成员 D 当前进度与下一阶段顺序

更新日期：2026-10-28。依据 GitHub PR 的最新源码、文档及状态复核；本文件调整 D 的计划，不表示其他分支已合入或已完成联合验收。

## 团队交付快照

| 成员 / PR | 本次查看的版本与状态 | 对 D 的实际影响 |
|---|---|---|
| [A / #1](https://github.com/SiriThree/PaperPilot/pull/1) | 执行前复核为 `member-a@0b2953c`，Open / Draft | 已提供 PostgreSQL 16、psycopg3、24 表迁移、26 方法 DataPort、DataBinding、隔离库 provider 和 outbox 投递器；最新另完成性能基线及备份恢复记录。移除“等待 A 实现数据层/provider/outbox”的旧前置条件；正式受限角色、统计视图、RLS/可信项目上下文和 outbox 保留/死信仍待 A/D 完成。 |
| [B / #3](https://github.com/SiriThree/PaperPilot/pull/3) | 已合并到 `main@4770732`；本轮已同步 D 分支并回归 | 业务反馈、来源校验和查询/审计页面已更新，页面明确区分 allowed 与 executed；D 无需另做前端。 |
| [B / #5](https://github.com/SiriThree/PaperPilot/pull/5) | 已合并到 `main@177dddc`；本轮已同步 D 分支并回归 | 新增研究流程衔接、Mock 安全证据展示与报告测试；不代表真实 SQL/数据库隔离已验收。 |
| [C / #4](https://github.com/SiriThree/PaperPilot/pull/4) | `feat/member-c-ai@1f43093`，Open / 非 Draft，未合并 | 已提供五方法真实 AI、PDF reader、检索和受控生成；`app.ai.bootstrap.create_app` 接受 `data_binding` 与 `security_factory`。可用于最终组合，当前没有 NL2SQL 方法，D 的三条固定统计不以 C 的模型或向量索引为前置。 |
| [D / #2](https://github.com/SiriThree/PaperPilot/pull/2) | `feature/member-d-security@2f781e0`，Open / Draft | 任务 1/2 与任务 3 校验阶段已交付。真实 SQL 始终未执行，受限执行器、关键审计、真实 D provider 及正式攻防尚未完成。 |

来源固定在本次查看的提交：[A 数据层记录](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/docs/data-adapter.md)、[A 正式迁移](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/backend/app/data/migrations/001_initial.sql)、[A 统计与审计实现](https://github.com/SiriThree/PaperPilot/blob/808ced98b76544bbfc3861f625fac313c2dabdb1/backend/app/data/adapter.py)、[C 接入说明](https://github.com/SiriThree/PaperPilot/blob/1f430936acf23c598a91ce794d70ca8c536d39e4/docs/ai-adapter.md)、[C 组合入口](https://github.com/SiriThree/PaperPilot/blob/1f430936acf23c598a91ce794d70ca8c536d39e4/backend/app/ai/bootstrap.py)。

A 的 41 项真实库共享测试、9 项业务联调及 5 项 outbox 测试，C 的 56 项真实适配器/替身契约测试均为各 PR 的交付记录，本轮没有在 D 环境复跑，不能替代 A/D 安全验收。C 的真实模型也不代表已提供 SQL 生成能力。

## D 任务状态

| 原任务 | 当前状态 | 调整后的剩余内容 |
|---|---|---|
| 1．SecurityPort 与构造入口 | 已完成 | 保留接口与显式工厂；联合入口另在任务 9 验证。 |
| 2．应用层授权策略 | 已完成 | 接 A 的正式 DataPort 回归；数据库受限身份/RLS 列入任务 4/8，不以应用层通过代替数据库证明。 |
| 3．自然语言统计网关 | 校验阶段已完成 | 新增正式 PostgreSQL/psycopg3 与统计视图适配待办，特别是多主题计数；现有中立 AST 规则保留，继续 not_executed。 |
| 4．受限数据库执行器 | 未实现；可开始 A/D 对齐 | 使用独立最小权限身份和受控视图；落实项目隔离、参数绑定、连接池上下文复位、3 秒超时、100 行及响应体限制。A 已有业务连接池不表示 D 可直接用其默认 postgres 身份执行候选查询。 |
| 5．执行结果与异常映射 | 部分完成 | 现有拒绝、未执行与网关错误已覆盖；真实成功、数据库故障、超时/取消、结果类型及限量处理随任务 4 实现和验证。 |
| 6．关键安全审计 | 未实现；优先于真实执行启用 | 复用 A 的 append_audit，建立 D 执行前/后的关键事件与失败关闭；A 的 outbox 已实现，不再重复建设投递器，但保留/死信、身份与防篡改权限仍需共同确认。 |
| 7．隔离 SecurityHarness provider | 未实现；A 的基础设施已可参考 | 复用 A 的一次性数据库及正式迁移，构建 D 自己的正常/拒绝/失败/不可达 fixture；测试库管理员连接只做初始化，不能冒充受限执行身份。 |
| 8．真实安全测试 | 待真实执行链路 | 保留现有 44 条拒绝与 21 条合法语法单元样例；在真实受限身份下另跑至少 40 攻击 + 20 正常用例及绕过、撤权、审计故障、资源限制。 |
| 9．团队交付与联调 | 文档已交付，联合运行待完成 | 先 A+D、C=mock，再组合 C 的真实实现；B 已提供页面状态，无需 D 改前端；正式认证仍需 A/B/D。 |

## 下一步顺序与完成门槛

1. **同步 B 的已合并基线并复验：已完成。** 本轮从 origin/main 合入 `4770732`，无文本冲突；Python 3.12 默认后端 371 passed / 37 skipped，前端类型及构建、公开契约和静态检查通过。A/C/D 最终合入时仍须同时保留 D 的 sqlglot、A 的数据层依赖及 C 的 pypdf/可选 AI 配置，不以覆盖整个 pyproject/锁文件处理差异。
2. **冻结 A/D 接入约定和任务 3 的正式模板：已形成待确认草案。** 详见 [A/D 接入草案](ad-security-contract-proposal.md)，包含双视图行粒度、psycopg 参数、角色/项目隔离、审计和资源边界。最新 A PR 及其评论未提供双方确认记录；需要 A/团队确认，或用户授权先在一次性数据库验证草案，不能将该步骤标为已冻结。确认前不把当前 `:project_id` 模板原样送给驱动。
3. **先完成任务 6 的审计失败关闭。** 可先用隔离 DataPort/可控依赖验证；A 代码集成后再验证真实持久化。执行前必须确认关键事件已写入；执行后审计无法确认则不返回结果。异步 outbox 和 B 的 requested/finished 都不能替代这一门槛。
4. **一起实现任务 4/5，并配套任务 7。** 先保持真实执行默认关闭，在隔离库用受限身份接通成功、失败、超时/取消、结果过滤及资源清理；每项都有对应测试再形成可评审提交。
5. **完成任务 8，再进行任务 9 联调。** 数据库权限、直接绕过网关、跨项目、审计和资源限制实测通过后才考虑启用执行。C 的真实模型/本地权重和 A/C 持久化向量索引不作为前三种统计的必需条件。

## 统计语义需要先解决的具体差异

- A 的 `papers.metadata_id` 是文献元数据身份，公开 paper_id 来自 `project_papers.paper_id`；统计必须通过项目内关系连接，排除 `project_papers.removed_at IS NOT NULL`。
- A 用 `topics + paper_topics` 表达多对多主题，现有按主题统计为每主题 `COUNT(DISTINCT pp.paper_id)`。D 当前草案把 year/topic/has_evidence 放在同一逻辑视图，若按主题展开会使按年份 COUNT(*) 和未提取列表重复。应先与 A 确定独立的论文/主题视图或等价防重复方案，再调整 AST 允许清单；本轮未擅自选定新视图或放宽 SQL 范围。
- A 当前 DataPort 没有主题写入方法，C 的 ProcessResult 也不提交主题标签。需明确主题数据来源；尚无主题时允许真实空结果，不能填入 Mock 主题来伪装真实统计。
- A 的未提取统计用同项目、同论文 `task_results.kind='evidence'` 的存在性判断。D 的 has_evidence 应对齐此语义；C 返回不足且已保存证据记录，不等于“尚未提取”，也不代表研究结论得到验证。
- 正式迁移已经有 `audit_events` 和 `outbox_events`，没有创建 D 草案视图、正式 RLS 策略或受限角色。表的不可变触发器及 day0 RLS 样例不构成正式最小权限/防篡改验收；SQL 读取身份和审计写入权限应分离。

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

B 的安全证据报告范围仍为隔离 Mock 应用，D 的真实 SQL 继续 not_executed。用户已要求依据 A 的实现落实第二步，后续将落地双视图、PostgreSQL/psycopg 参数及隔离验证，不把 A 尚未给出的正式部署审批写成已获批准。
