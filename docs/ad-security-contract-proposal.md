# A/D 统计与受限执行接入草案

日期：2026-10-28。状态：D 侧待确认草案，不是双方已冻结的协议；不授权修改 A 的迁移、业务库或正式数据库角色，也不启用 SQL 执行。

## 依据与边界

已核对 A 的 [最新实现记录](https://github.com/SiriThree/PaperPilot/blob/0b2953c218668af71a4bf869e5c6d238cc0d588d/docs/data-adapter.md) 及 [controlled_statistics / append_audit](https://github.com/SiriThree/PaperPilot/blob/0b2953c218668af71a4bf869e5c6d238cc0d588d/backend/app/data/adapter.py#L962)。A 仍将受限角色、视图、RLS/可信项目上下文列为协作项，PR #1 当前没有确认这些约定的评论。

B 的 main@4770732 已同步到 D。保持 AB-1 的 26 方法、SecurityPort 两方法和公开 Schema 不变；候选 SQL 只在 D 内部处理，不使用 DataPort.controlled_statistics 替代动态查询的受限执行验收。

## 1．建议的统计视图与语义

以下名称为建议，可由 A 调整；确定后同步修改 D 的固定 AST 模板及测试，不支持运行时由请求指定表名。

| 建议对象 | 行粒度与字段 | 必须满足的语义 |
|---|---|---|
| paperpilot_security.paper_statistics | 每个有效项目导入身份一行：project_id、paper_id、title、year、has_evidence | paper_id 来自 project_papers，按项目和 metadata_id 连接 papers；排除 removed_at 非空；不连接主题展开行。year 可空，按年份统计保留空值组，与 A 当前实现对齐。 |
| paperpilot_security.paper_topics | 每个项目内 paper_id/topic_id 关联一行：project_id、paper_id、topic_id、topic | 来自有效 project_papers、paper_topics 和 topics；排除已移出论文；每主题按 COUNT(DISTINCT paper_id) 计数，多主题论文可在各主题各计一次。 |

has_evidence 为同项目、同论文是否存在已提交的 task_results.kind='evidence'。已有证据但字段为不足/未提及，仍视为存在；不据此宣称证据质量通过。当前没有主题写入面，主题空结果必须真实保留，不使用合成主题补齐。

采用两种行粒度可避免“主题展开导致年份 COUNT(*) 和未提取列表重复”。待 A 确认后再移除旧的单一 security_paper_statistics 草案模板；不直接给候选开放底层业务表和任意 JOIN。

## 2．驱动、身份和项目隔离

- 目标为 A 当前 PostgreSQL 16 + psycopg3。QueryPlan 保留参数与 SQL 分离；执行器适配 psycopg 命名参数格式并校验绑定，不能直接执行当前 SQLGlot 的中立 :project_id 文本，也不能字符串拼接用户/项目值。
- 查询连接必须使用独立最小权限登录身份：不是 postgres、表所有者或超级用户，无 BYPASSRLS、建库、建角色和 DDL 权限，不继承业务写角色；读取范围仅限批准统计字段。
- 项目来自 B 的可信 Context，D 在调用前重新检查当前成员关系。数据库层还必须阻止该读取身份直接跨项目读取，不能只依赖候选中的 WHERE。
- **需要 A/D 选择项目绑定方式**：按项目隔离的受限登录角色/连接，或共享读取身份加不可由该身份任意伪造的可信上下文机制。普通可随意 SET 的项目变量本身不能作为防绕过证明。A 的 day0 独立登录角色样例仅可参考，不能原样当作正式用户/项目生命周期设计。
- D 建议先在一次性数据库中按 day0 已验证的“独立项目登录角色”做隔离原型，验证两个项目、无应用过滤的直接查询和越权拒绝；正式角色生命周期及生产方案仍由 A/D 确认。
- 每次查询独立只读事务，资源配置和角色选择只来自可信组合入口。会话设置限于本事务；失败、取消或超时后先确认回滚/关闭，连接状态不确定时不得归还供后续请求使用。

## 3．执行级关键审计

复用 A 的 append_audit 和 event_id 幂等语义，不新建第二套用户/审计表或重复开发 outbox 投递器。

1. 执行前追加 D 的关键事件并确认成功；审计不可用时不调用执行器。
2. 执行成功后追加结果状态事件并确认成功，再向 B 返回结果；确认失败不返回行数据，不声称此前 SQL 没有运行。
3. 执行异常、超时和取消也记录对应事件；错误脱敏，审计中不保存候选 SQL、凭据和未授权内容。request_id 关联整个请求，事件使用各自的 event_id。
4. 只读 SQL 身份与审计写入权限分离。审计连接/受控写入身份的配置及保护由 A/D 对齐，不给统计读取身份直接修改审计和 outbox 的权限。

A 的业务 outbox 为独立异步投递，B 的 requested/finished 为业务日志，均不能替代执行前的确认门槛。现有 published 无限保留策略先保持；本草案不新增自动删除、过期或死信清理。保留/死信策略后续单独确认。

## 4．结果与资源限制建议

- 已约定目标：数据库端 SQL 超时不超过 3 秒、最多 100 行。应用端同时处理超时/取消并清理数据库操作，不能仅超时返回而留下后台查询。
- 建议响应序列化大小上限为 256 KiB，作为待确认的执行器配置；超过上限返回安全失败，不返回部分结果，不擅改公开 Schema。
- columns/rows 只含批准字段与公开 Schema 的基础类型；UUID 等驱动对象在 D 边界转换，内部 SQL、异常正文与连接对象不得返回。
- 只有执行完成且关键审计已确认才返回 allowed/executed；拒绝为 rejected/not_executed，真实执行故障为 undetermined/failed，网关不可用为脱敏 AppError。未接入时继续 not_executed，不降级 Mock。

## 5．确认与实施门槛

仍需 A 或团队明确确认：

1. 两种视图的名称、行粒度、字段与主题/空值/证据语义。
2. 受限登录身份与项目绑定方案、RLS/视图权限组合，以及初始化、撤销和连接复用的责任。
3. 执行级审计写入身份与保护、失败确认语义，以及建议的响应大小上限。

确认前可选择的下一步为：由用户提供 A 的确认记录；或者明确授权 D 按上述建议先实现一次性数据库原型，保持正式执行关闭，将原型结果交 A 评审。未经确认不把正式接入约定标记为完成，不发布对外协调信息。

确认后按进度文档继续：关键审计 → 受限执行器和结果映射（配套 SecurityHarness）→ 真实受限身份的 40+20 攻防 → A+D 再 A+C+D 联调。本机 Docker 引擎尚不可达且无 day0 测试配置，进入真实库原型前还需建立独立测试环境；不得改用用户业务库代替。
