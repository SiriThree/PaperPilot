# PaperPilot 数据库设计分层汇报稿

核对日期：2026-10-28。本文对照《第五章 数据库设计》的概念结构设计与关系模型转换部分，结合仓库当前 PostgreSQL 迁移和数据适配器整理，可直接用作汇报提纲、讲稿和答辩备查。

本文按“业务需求 → 局部 E-R → 总体联系 → 冲突消解 → 关系模式 → 完整性 → 规范化与冗余 → 实现验证”展开。课件是设计方法的参考，项目事实以当前代码和 AB-1 为依据。

## 1. 汇报路线：八层内容，主讲与备查分开

| 层次 | 回答的问题 | 主讲内容 | 展示材料 |
| --- | --- | --- | --- |
| 第一层：业务与范围 | 为什么需要这些数据？ | 项目内协作文献研究；数据隔离、证据追溯、报告留史、任务可恢复 | 一句话场景与业务链路 |
| 第二层：实体与局部视图 | 有哪些对象，怎样分块建模？ | 身份与项目、文献与来源、研究与报告、运行控制与审计 | 四张分 E-R 图 |
| 第三层：联系与基数 | 对象怎样联系，数量有什么限制？ | M:N、1:N、可选 1:1；最小参与另行解释 | 总 E-R 图及联系清单 |
| 第四层：合并与冲突 | 为什么要统一、拆分和改名？ | 命名冲突、属性冲突、结构冲突，补充冗余分析 | 三个重点案例 |
| 第五层：关系模式 | E-R 怎样变成数据库表？ | 实体映射；M:N 转关联表；1:N 外键放多端；依附对象用组合键 | 24 张表分组、四个转换例子 |
| 第六层：完整性约束 | 怎样保证数据合法且相互一致？ | 实体完整性、参照完整性、用户定义完整性；动态业务规则 | 约束分类表、负责人 SQL |
| 第七层：范式与受控冗余 | 为什么这样拆表，有哪些有意重复？ | 函数依赖、异常消除、项目维度、版本指针、历史快照 | 依赖例子与冗余表 |
| 第八层：落实与证据 | 设计是否落地，边界在哪里？ | PostgreSQL 约束 + Python 事务；并发、回滚和历史验证；待协作事项 | 一次导入/保存流程与已有记录 |

课件对应：第 49、53—56 页为局部视图设计；第 67—74 页为视图集成及三类冲突；第 75—80 页为冗余与依赖；第 86 页为整体概念结构验证；第 89—99 页为关系模型转换；第 104—108 页为模型优化。这里的页码是 PDF 文件页码。

完整性部分是在课件建模方法上，结合本项目主外键、值域、业务事务补充的归纳，不声称课件上述页码逐项列出了本项目规则。

## 2. 第一层：先讲业务，不先报表名

PaperPilot 的数据主线是：

> 用户参与研究项目，在项目内导入论文；论文处理形成可定位的原文片段；研究任务产生问答、证据或草稿；保存报告时生成不可覆盖的版本及引用快照；执行过程由任务尝试、幂等记录、领域事件和审计共同追踪。

四条核心需求：

1. **项目隔离**：同一用户可参加多个项目，同一文件可以分别导入不同项目，每次导入独立授权。
2. **来源追溯**：研究结果中的引用能定位到具体论文、片段、页码和摘录。
3. **历史稳定**：论文移出或重新处理后，已保存报告的正文和引用快照仍保持原样；历史读取仍要求当前项目权限。
4. **可靠执行**：重复请求不重复创建业务对象，失败重试不更换任务身份，旧 worker 不能覆盖新尝试。

## 3. 第二层：四个局部 E-R 视图

| 视图 | 主要概念对象 | 设计重点 | 图 |
| --- | --- | --- | --- |
| V1 身份与项目 | 用户、项目；参与联系及项目角色 | 角色属于参与关系；一个项目恰好一个负责人 | [身份与项目](diagrams-chen-clean/v1-identity-project.svg) |
| V2 文献与来源 | 论文、作者、主题、原文片段、向量模型 | 区分导入身份与元数据；作者/主题/模型为多对多；来源有稳定定位 | [文献与来源](diagrams-chen-clean/v2-literature-source.svg) |
| V3 研究与报告 | 任务、研究结果、证据字段、结果来源、报告、版本、版本来源 | 任务范围、五类证据、引用作用域、历史快照 | [研究与报告](diagrams-chen-clean/v3-research-report.svg) |
| V4 运行控制与审计 | 执行尝试、幂等记录、审计事件、outbox 事件 | 领取与租约、迟到写入隔离、重放、业务与事件原子提交 | [运行控制与审计](diagrams-chen-clean/v4-control-audit.svg) |

**怎样判断实体和属性？** 有独立标识、生命周期或需参与其他联系的对象适合建实体；页码、状态等作为属性。作者需被多篇论文关联，不能只当作论文中的姓名字符串；作者顺序依赖具体“论文—作者”组合，因此是联系属性。

现有 [总 E-R 图](diagrams-chen-clean/total-er.svg) 有 **18 个概念实体、28 个联系**；转换后的逻辑模式有 **24 张业务表**。关联表在概念图中画成菱形联系，论文在实现层拆成元数据和导入记录，因此实体数与表数不同。迁移器另用 `schema_migrations` 记录版本，不计入这 24 张业务表。

现有五张图已经吸收冲突消解结论。汇报建模过程时，可讲“先划分局部视图，再集成并消解冲突”，但不要把当前分图当作仍存在旧歧义的原始草稿。历史初步合并图已移除，过程依据是合并对应表与冲突记录。

## 4. 第三层：到底有哪些联系？

下面按总图列全 28 个联系。`1:N`、`M:N` 首先说明最大基数；“是否必须存在”“至少多少个”在说明列补充。这里的“论文”均指项目内的一次导入身份。

### 4.1 身份、项目与运行归属：7 个

| 联系名 | 实体及基数 | 含义与限制 |
| --- | --- | --- |
| 参与 | 用户 M:N 项目 | 用户可以参加多个项目，项目可有多个用户；role、joined_at 是联系属性 |
| 幂等归属 | 项目 1:N 幂等记录 | 每条幂等记录必须归属一个项目 |
| 提交请求 | 用户 1:N 幂等记录 | 每条幂等记录对应一个请求用户；不是“一个 request_id 一条记录” |
| 审计归属 | 项目 1:N 审计事件 | 项目可以有多条审计；登录等事件的 project_id 可空 |
| 执行动作 | 用户 1:N 审计事件 | 用户可以产生多条审计；匿名/失败认证等事件的 actor_id 可空 |
| 事件归属 | 项目 1:N outbox 事件 | 一个项目可产生多条领域事件；模式允许 project_id 为空 |
| 调度 | 项目 1:N 任务 | 每个任务只属于一个项目，项目可以暂时没有任务 |

### 4.2 文献与操作者：8 个

| 联系名 | 实体及基数 | 含义与限制 |
| --- | --- | --- |
| 上传 | 用户 1:N 论文 | 每次导入记录一位上传人；历史上传人关联 users，不依赖当前成员行 |
| 发起 | 用户 1:N 任务 | tasks.actor_id 是当前尝试关联的操作者，重试可以更新；旧尝试另存原上下文 |
| 保存 | 用户 1:N 报告版本 | 每版记录保存者，历史不随撤销成员而删除 |
| 导入 | 项目 1:N 论文 | 每次导入只属于一个项目；同一文件跨项目生成独立 paper_id |
| 撰写 | 论文 M:N 作者 | 一篇论文可有多个作者，作者可对应多篇；author_order 属于联系 |
| 归类 | 论文 M:N 主题 | 一篇论文可有多个主题，一个主题可包含多篇论文 |
| 切分 | 论文 1:N 原文片段 | 片段必须属于一篇论文；尚未处理的论文可以没有片段 |
| 向量化 | 原文片段 M:N 向量模型 | 每个“片段、模型”组合最多一条向量；向量值、生成时间是联系属性 |

### 4.3 报告与历史来源：5 个

| 联系名 | 实体及基数 | 含义与限制 |
| --- | --- | --- |
| 管理报告 | 项目 1:N 报告 | 每份报告只属于一个项目 |
| 保存版本 | 报告 1:N 报告版本 | 已提交报告至少一版；版本号在同一报告内唯一并连续递增 |
| 基于 | 报告版本之间可选 1:1 自联系 | 首版无前版；后续版恰有一个前版；每版最多一个直接后继，形成线性链 |
| 保留快照 | 报告版本 1:N 版本引用快照 | 当前保存契约允许每版 0—100 条来源，不能说每版必须有引用 |
| 快照定位 | 原文片段 1:N 版本引用快照 | 每条快照定位一个片段；同片段可被多个版本引用；快照另存保存时的页码和摘录 |

“基于”是线性版本链上直接前驱/后继的限制，整份报告仍然可以有很多版本。

### 4.4 研究任务与结果来源：8 个

| 联系名 | 实体及基数 | 含义与限制 |
| --- | --- | --- |
| 选定范围 | 任务 M:N 论文 | 一篇论文可参与多个任务；process/evidence 恰好一篇，answer/draft 允许零篇 |
| 领取 | 任务 1:N 执行尝试 | 未领取任务可有零次尝试；每次领取增加尝试号；同任务至多一个 processing 尝试 |
| 产生 | 任务 1:0..1 研究结果 | 每任务最多一个已提交成功结果；succeeded 必须恰好一个，其他状态无成功结果 |
| 采纳 | 执行尝试 1:0..1 研究结果 | 每个结果必须注明一个被采纳的成功尝试；失败/过期尝试没有成功结果 |
| 提取 | 研究结果 1:N 证据字段 | 仅 evidence 结果有字段，必须恰好五类；其他结果不带证据字段 |
| 引用 | 研究结果 1:N 结果来源 | 一个结果可以有多条来源；是否可为空取决于任务类型和证据不足标志 |
| 支撑 | 证据字段 1:N 结果来源 | evidence 来源属于一个字段；其他类型的 root 来源不属于任何证据字段 |
| 定位 | 原文片段 1:N 结果来源 | 每条来源定位一个片段；同片段可以被多个结果、字段引用 |

### 4.5 总图省略但答辩可能问到的关系

- **负责人**：用户到项目的 1:N 特殊角色关系，由“参与联系的 role=owner”表达，避免再维护一套独立负责事实。数据库保留 owner_id 作为受约束的指针。
- **元数据与导入**：`papers` 与 `project_papers` 当前提交后一对一，每次导入独占元数据；概念层合成“论文”。同一摘要跨项目重复不表示共享同一 paper_id。
- **outbox 与审计**：按 event_id 做投递和去重关联，不是强制一一外键。投递前可以只有 outbox；审计也可独立追加。
- 作者、主题、向量模型等也有项目归属；总图省略部分辅助联系，完整归属由迁移中的复合外键表达。

## 5. 第四层：按课件分类讲冲突

这里的冲突是局部视图集成时的语义、表示或结构不一致。它与两个用户同时保存报告的运行期并发冲突，应分开讲。

### 5.1 命名冲突：名字是否表示同一个东西？

| 具体问题 | 冲突判断 | 消解方案 | 结果 |
| --- | --- | --- | --- |
| “论文编号”既表示书目，也表示某项目的导入 | 同名异义（C01） | 公开 paper_id 固定表示项目导入；内部书目使用 metadata_id | 不会把跨项目同文件当成同一授权资源 |
| 来源、片段、引用常被混用 | 需要拆清语义（C04） | chunk_id 标识稳定片段；Source 是结果上下文中的来源视图；citation_id 是局部引用编号 | 不会把 `[1]` 之类编号当全局主键 |
| 业务角色与数据库角色都称“角色” | 同名异义（C15） | owner/member 属于项目成员；PostgreSQL 登录角色属于数据库授权体系 | 不混淆应用权限与数据库权限 |

不同视图中的 `RESULT` / `TASK_RESULT` 等同义称呼可统一为一个概念；但不能仅因名字相近就把 Chunk、Source 和历史快照全部合并。

### 5.2 属性冲突：类型、范围、单位与作用域是否一致？

课件的严格分类是“属性域冲突”和“属性取值单位冲突”。本项目较典型的是属性域、空值含义和唯一作用域的统一；没有证据说明项目曾出现米/厘米这样的实际单位冲突，不必为了凑类别编造。

| 具体问题 | 统一规则 | 关联设计记录 |
| --- | --- | --- |
| 年份未知时，有的视角希望必填，有的允许缺失 | year 为 NULL 或 1000—3000，不用 0 伪造未知年份 | C07 |
| 同名“状态”涵盖处理、移出、索引和尝试 | 分开保存 status、removed_at、index_state；task 和 attempt 各有自己的状态集合 | C07/C08 |
| citation_id 在不同证据字段中重复 | 结果来源键为 `(task_id, scope, citation_id)`，字段内唯一；版本快照键为 `(version_id, citation_id)` | C05 |
| 向量维度及模型版本尚未冻结 | 模型单独登记 dimensions/revision；向量维度必须与对应模型一致，不把演示 3 维当正式值 | C12 |
| request_id 被误作重复请求唯一键 | request_id 关联一次请求的多个事件；幂等键为用户、项目、操作与 key 的组合 | C10 |

负责人重复表达也可在汇报中归入“属性一致性问题”：owner_id 与 role=owner 必须一致。但它不完全等于课件中类型/单位不一致的狭义属性冲突，同时涉及冗余和跨表完整性。

### 5.3 结构冲突：对象的抽象、联系和生命周期是否一致？

| 具体问题 | 不处理会怎样 | 消解方案 | 关联记录 |
| --- | --- | --- | --- |
| 一份全局共享书目，还是各项目独立导入？ | 一个项目的修改/删除影响另一个项目 | 首版每次导入独占元数据、作者/主题关联、片段和向量，跨项目不共享授权对象 | C02 |
| 当前来源与历史报告引用共用同一可变对象 | 论文重新处理后，旧报告引用跟着变 | 分开 result_sources 和 report_version_sources；保存完整引用值快照 | C06 |
| 一个任务被当作一次执行 | 重试后旧 worker 的结果覆盖新尝试 | tasks 保存任务身份与当前状态，task_attempts 保存每次租约与上下文，结果记录 committed_attempt | C08 |
| result_ref.resource_id 随类型指向不同表 | 普通外键无法同时正确指向论文、证据或任务 | 使用类型化结果容器与约束，公开 result_ref 按 kind 派生 | C09 |
| 历史操作者外键指向当前成员关系 | 撤销成员会破坏报告、任务与审计历史 | 历史 actor/creator 指向 users；成员删除只代表撤权 | C11 |
| 审计不可变与事件投递状态可变混在一张表 | 不是无法重试，就是放开了审计修改 | audit_events 追加事实，outbox_events 保存可重试投递状态，身份/载荷不可改 | C13 |
| 仅检查两个 UUID 各自存在 | 同一行可能连到另一项目的资源 | 用包含 project_id 的复合外键，同时校验资源存在和项目归属 | C14 |

**推荐主讲三个案例**：论文编号（命名）→ 引用编号作用域（属性）→ 实时来源与历史快照（结构）。负责人一致性单独放在“受控冗余与完整性”页更容易贴合课件分类。

## 6. 第五层：E-R 怎样转换为关系模式？

“联系”是概念层对象之间的语义关联；“关系模式”是逻辑层的表结构，可写成 `关系名(属性1, 属性2, …)`。二者不是同一个术语。

### 6.1 四个最值得展示的转换例子

| E-R 类型 | 本项目例子 | 转换后的关系模式和键 |
| --- | --- | --- |
| 实体 → 表 | 用户 | `users(user_id, username, display_name, created_at)`；PK=user_id，UQ=username |
| M:N → 关联表 | 用户参与项目 | `project_members(project_id, user_id, role, joined_at)`；PK=(project_id,user_id)，role/joined_at 为联系属性 |
| 1:N → 多端存外键 | 报告保存多个版本 | report_versions 中放 report_id；版本以 version_id 为 PK，另用 `(report_id,number)` 保证报告内版本号唯一 |
| 1:1 → 唯一外键或共享主键 | 任务产生结果 | task_results.task_id 既是 PK 又是 FK，保证每任务最多一个结果；成功任务必须有结果还需提交时一致性检查 |

补充两个联系属性示例：`paper_authors.author_order` 是论文—作者联系的属性；`chunk_embeddings.embedding` 是片段—模型联系的属性。

### 6.2 全部 24 个关系模式：核心属性版

以下是便于展示的关系模式摘要，**省略部分时间、指纹和辅助约束列**，不用于代替 DDL。完整列、可空性与外键见 [数据字典](04-data-dictionary.md)，实际定义见 [PostgreSQL 迁移](../../backend/app/data/migrations/001_initial.sql)。PK 列均非空。

#### A. 身份与项目：3 张

| 表 | 核心关系模式 | 主键与重要唯一性 |
| --- | --- | --- |
| users 用户 | `users(user_id, username, display_name)` | PK user_id；UQ username |
| projects 项目 | `projects(project_id, name, description, owner_id, owner_role)` | PK project_id；项目名不要求唯一 |
| project_members 成员 | `project_members(project_id, user_id, role, joined_at)` | PK (project_id,user_id)；每项目至多一条 owner |

#### B. 文献、分类与向量：9 张

| 表 | 核心关系模式 | 主键与重要唯一性 |
| --- | --- | --- |
| papers 书目元数据 | `papers(metadata_id, project_id, title, year)` | PK metadata_id |
| project_papers 项目导入 | `project_papers(paper_id, project_id, metadata_id, imported_by, digest, storage_key, status, index_state, active_process_task_id, removed_at)` | PK paper_id；UQ metadata_id、(project_id,digest) |
| authors 作者 | `authors(author_id, project_id, name)` | PK author_id；作者姓名不唯一 |
| paper_authors 论文作者 | `paper_authors(paper_id, author_id, project_id, author_order)` | PK (paper_id,author_id)；UQ (paper_id,author_order) |
| topics 主题 | `topics(topic_id, project_id, name)` | PK topic_id；UQ (project_id,name) |
| paper_topics 论文主题 | `paper_topics(paper_id, topic_id, project_id)` | PK (paper_id,topic_id) |
| chunks 原文片段 | `chunks(chunk_id, project_id, paper_id, page_number, excerpt, content, synthetic)` | PK chunk_id |
| embedding_models 向量模型 | `embedding_models(model_id, project_id, provider, model_name, revision, dimensions, distance_metric)` | PK model_id；UQ (project_id,provider,model_name,revision) |
| chunk_embeddings 片段向量 | `chunk_embeddings(chunk_id, model_id, project_id, dimensions, embedding)` | PK (chunk_id,model_id) |

#### C. 任务、结果与证据：6 张

| 表 | 核心关系模式 | 主键与重要唯一性 |
| --- | --- | --- |
| tasks 任务 | `tasks(task_id, project_id, actor_id, request_id, kind, status, attempts, payload, error_code, error_message)` | PK task_id |
| task_papers 任务论文范围 | `task_papers(task_id, paper_id, project_id)` | PK (task_id,paper_id) |
| task_attempts 执行尝试 | `task_attempts(task_id, attempt_no, project_id, actor_id, request_id, status, claimed_at, lease_expires_at, finished_at)` | PK (task_id,attempt_no)；同任务至多一个 processing |
| task_results 研究结果 | `task_results(task_id, project_id, kind, committed_attempt, paper_id, evidence_id, payload)` | PK task_id；非空 evidence_id 全局唯一 |
| evidence_fields 证据字段 | `evidence_fields(task_id, field_name, project_id, text, status)` | PK (task_id,field_name) |
| result_sources 结果来源 | `result_sources(task_id, scope, citation_id, project_id, field_name, ordinal, paper_id, chunk_id, page_number, excerpt, synthetic)` | PK (task_id,scope,citation_id)；UQ (task_id,scope,ordinal) |

#### D. 报告与历史：3 张

| 表 | 核心关系模式 | 主键与重要唯一性 |
| --- | --- | --- |
| reports 报告 | `reports(report_id, project_id, created_by, current_version_id)` | PK report_id |
| report_versions 报告版本 | `report_versions(version_id, project_id, report_id, number, base_version_id, title, research_question, body, created_by)` | PK version_id；UQ (report_id,number)、非空 (report_id,base_version_id) |
| report_version_sources 版本来源快照 | `report_version_sources(version_id, citation_id, project_id, ordinal, paper_id, chunk_id, page_number, excerpt, synthetic)` | PK (version_id,citation_id)；UQ (version_id,ordinal) |

#### E. 幂等与审计：3 张

| 表 | 核心关系模式 | 主键与重要唯一性 |
| --- | --- | --- |
| idempotency_records 幂等记录 | `idempotency_records(user_id, project_id, operation, key, request_fingerprint, response_snapshot, paper_id, task_id, version_id, expires_at)` | PK (user_id,project_id,operation,key) |
| audit_events 审计事件 | `audit_events(event_id, timestamp, request_id, actor_id, project_id, action, resource_type, decision, execution_status, policy_id, duration_ms, mock)` | PK event_id；request_id 不唯一 |
| outbox_events 待投递事件 | `outbox_events(event_id, project_id, actor_id, request_id, payload, delivery_attempts, next_attempt_at, published_at)` | PK event_id；不强制引用 audit_events |

合计：3 + 9 + 6 + 3 + 3 = **24 张**。这五组方便展示关系表，不改变前面四个业务局部视图的划分。

## 7. 第六层：完整性约束有哪些？

### 7.1 三类基本完整性

| 类别 | 含义 | 本项目例子 | 实现位置 |
| --- | --- | --- | --- |
| 实体完整性 | 每行可被唯一标识，主键不能空 | user_id、paper_id、version_id；成员组合键、来源组合键 | PRIMARY KEY |
| 参照完整性 | 引用目标存在，关联归属正确 | 片段指向论文；版本指向报告；论文作者的两端必须同项目 | FOREIGN KEY，含项目维度的复合 FK |
| 用户定义完整性 | 业务规定的值域、唯一性与数量限制 | 年份、页码、摘要格式；项目摘要唯一；恰好一位 owner；证据五字段 | NOT NULL、UNIQUE、CHECK、部分唯一索引、延迟约束与可信事务入口 |

用户名唯一属于业务唯一性/候选键约束；不能把所有 UNIQUE 都笼统等同于“主键完整性”。

### 7.2 静态值域与唯一性

| 对象 | 约束 |
| --- | --- |
| 用户/项目 | username 唯一，1—64 字符；项目名 1—100，描述最多 1000；角色只允许 owner/member |
| 论文 | digest 为 64 位小写十六进制；year 为 NULL 或 1000—3000；`(project_id,digest)` 唯一，移出后仍占用 |
| 片段/来源 | page_number ≥ 1；excerpt 1—4000 字符；来源顺序 ordinal ≥ 1 且作用域内不重复 |
| 任务/尝试 | kind、status 使用各自限定集合；attempts ≥ 0；attempt_no ≥ 1；processing 任务 attempts ≥ 1 |
| 租约 | lease_expires_at > claimed_at；processing 尝试 finished_at 为空，结束尝试需有结束时间 |
| 模型/向量 | dimensions > 0；`vector_dims(embedding)=dimensions`；向量登记的维度通过复合 FK 匹配模型 |
| 审计 | event_id 唯一；duration_ms 非负且有限；request_id 可关联多条事件 |

### 7.3 跨表、数量与生命周期约束

| 规则 | 怎样保证 | 汇报要点 |
| --- | --- | --- |
| 每项目恰好一个 owner，且是成员 | owner 部分唯一索引保证至多一个；projects 非空 owner_id/固定 owner_role 配合延迟复合 FK 保证负责人存在且角色正确 | 唯一索引单独只能保证“至多”，不能保证“至少” |
| 元数据与项目导入一对一 | metadata_id 非空唯一 FK；插入 papers 的延迟存在性触发器检查配对导入 | 同事务插入配对数据，提交时校验 |
| 跨项目引用不可拼接 | 如 `(project_id,paper_id,chunk_id)` 复合 FK 指向同一实际片段 | 只检查 paper_id 与 chunk_id 分别存在还不够 |
| 成功任务与成功结果一致 | 共享主键保证最多一个结果；延迟触发器和完成事务校验成功状态、结果类型与被采纳尝试 | “最多一个”与“成功时必须有一个”分开说明 |
| 证据恰好五字段 | 字段名 CHECK + 组合 PK + 延迟检查五行；pending_verification 要求文本及支撑来源 | 五类为研究问题、方法、结果、结论、局限 |
| 引用真实且属于任务范围 | 适配器重查当前项目、论文、片段、页码、摘录、synthetic 与任务论文范围 | 外键只能证明关联存在，不能证明 AI 输出语义一致 |
| 报告版本连续且不分叉 | 唯一版本号、唯一前版、同报告自 FK、连续性触发器；保存时锁报告行并比较 base_version_id | 旧基础版本保存返回 VERSION_CONFLICT |
| 已保存历史不可覆盖 | report_versions/report_version_sources 禁止 UPDATE/DELETE；片段关键内容禁止修改并禁止删除；历史 FK 不级联清理 | 新处理创建新片段；历史快照读保存时的值 |
| 业务与领域事件一致 | 业务变更和 outbox INSERT 在同一数据库事务 | 投递独立重试，不能假设后补 audit 与业务同事务 |

跨表存在性和计数规则不能直接写成普通 CHECK。本项目在需要时使用可延迟外键/约束触发器，在事务提交处检查配对数据。

### 7.4 动态业务约束与并发冲突

| 场景 | 必须保持的规则 | 手段/行为 |
| --- | --- | --- |
| 重复导入 | 同项目同摘要只能保留一次导入 | 唯一键裁决并发竞争；DUPLICATE_PAPER；跨项目相同摘要可以分别导入 |
| 重复请求 | 同键同参数重放原响应，同键异参数拒绝 | 幂等组合 PK + 规范化指纹；IDEMPOTENCY_CONFLICT；重放仍检查当前权限 |
| 多 worker 领取 | 同次任务只有一个领取者 | 行锁、FOR UPDATE SKIP LOCKED；尝试部分唯一索引 |
| 失败重试与租约恢复 | 复用 task_id，尝试号递增；旧结果不能覆盖新结果 | retry 仅 failed→pending；恢复封存过期尝试；完成时核验当前 attempts、状态与上下文 |
| 并发保存报告 | 两人基于同一版本保存，只允许一个推进版本链 | 锁报告行、比较基础版本及唯一约束；另一方 VERSION_CONFLICT |
| 成员撤销 | 后续访问和成功提交重新检查权限 | 数据层重验成员；历史保留不代表撤权用户仍有读取权 |
| 论文移出 | 当前查询隐藏，旧报告快照不变 | removed_at 软删除；新保存重验来源；旧版本按当前项目权限读取 |
| 提交确认丢失 | 不能误判已回滚而删掉被采纳文件 | DATA_COMMIT_UNKNOWN；沿用原幂等键对账/重放，暂存文件补偿与数据库事务分开 |

这些规则由“数据库约束 + SQL 锁/条件更新 + Python 输入与授权检查 + 事务”共同实现，不能说成每条都只靠外键或 CHECK。

## 8. 第七层：规范化、函数依赖与冗余

### 8.1 可以直接在黑板上写的函数依赖

```text
user_id → username, display_name, created_at
username → user_id                    （username 是候选键）
(project_id, user_id) → role, joined_at
(paper_id, author_id) → author_order
(paper_id, author_order) → author_id   （作者顺序唯一）
(task_id, attempt_no) → claimed_at, lease_expires_at, status, …
(task_id, scope, citation_id) → chunk_id, page_number, excerpt, …
version_id → report_id, number, body, …
(report_id, number) → version_id
```

关键解释：role 不由 user_id 单独决定；同一用户在不同项目中角色可以不同。author_order 不由 author_id 单独决定；同一作者在不同论文中的顺序可以不同。

将多对多拆成关联表，避免在一列中保存逗号分隔的作者、主题或论文编号；将报告版本拆开，避免新版本覆盖旧版本；将任务和尝试拆开，避免重试覆盖执行历史。这些拆分减少插入、更新与删除异常。

### 8.2 哪些重复是有意保留的？

课件第 76、78、108 页允许基于应用需要保留冗余，前提是说明它的用途和一致性维护方式。本项目的重复分三种：

| 保留内容 | 用途 | 一致性方式 |
| --- | --- | --- |
| 子表中的 project_id | 便于项目筛选、显式复合 FK 校验和授权检查 | 与资源键一起引用父表；不是客户端随意填写就可信 |
| projects.owner_id 与成员 owner 角色 | 直接定位负责人，同时保留成员模型 | 固定 owner_role、延迟复合 FK、部分唯一索引、负责人不可变规则 |
| reports.current_version_id | 快速取当前版本并作为并发控制入口 | 延迟 FK、版本链检查、同事务更新及报告行锁 |
| task_results.payload 与类型化字段/来源行 | 保留完整契约载荷，同时支持结构化查询与约束 | 适配器同事务分解写入；结果读取重装配，不让两份数据分别修改 |
| 版本快照中的页码、摘录和 synthetic | 保存历史时点事实 | 保存时重验后冻结；后续不应自动同步成当前来源值 |
| 幂等响应快照 | 重放首次成功响应 | 与业务同事务保存；状态变化后也不替换原响应 |

历史快照代表“当时的值”，不是应该始终追随当前行的缓存；这是保留重复数据时最重要的语义区别。

**范式答辩口径**：核心业务依赖按规范化方法组织，多对多和不同生命周期分离；物理关系中保留受约束的项目维度、指针与快照。不能未经逐表证明就宣称“24 张表全部严格满足 3NF/BCNF”。例如 `paper_authors` 的非主属性 project_id 可由 paper_id 单独决定，相对组合主键存在部分依赖，说明实现表含有明确的反规范化选择。可以解释其用途及复合外键如何防止归属矛盾。

### 8.3 四个容易出现的“业务矛盾”及处理

1. **同文件跨项目允许，但同项目禁止重复**：唯一性范围是 `(project_id,digest)`，不是全库 digest。
2. **成员撤销要立即生效，但历史必须保留**：删除成员行改变权限，历史操作者仍指向 users；读取时重验当前成员资格。
3. **论文移出后不能继续研究，但旧报告要可追溯**：当前访问看 removed_at，历史报告读冻结快照；项目授权仍有效。
4. **失败可重试，但成功结果不能重复或被迟到覆盖**：稳定任务身份 + 递增尝试号 + 当前尝试条件提交；外部 AI 调用可能重复，不承诺端到端恰好一次。

## 9. 第八层：实现与验证怎样展示？

### 9.1 用一条 SQL 说明“设计不是停留在图上”

当前迁移中有以下负责人约束：

```sql
CREATE UNIQUE INDEX one_owner_per_project
    ON project_members (project_id) WHERE role = 'owner';

ALTER TABLE projects ADD CONSTRAINT projects_owner_is_member
    FOREIGN KEY (project_id, owner_id, owner_role)
    REFERENCES project_members (project_id, user_id, role)
    DEFERRABLE INITIALLY DEFERRED;
```

配合 projects.owner_id 非空、owner_role 固定为 owner，第一部分保证“最多一个”，第二部分保证“指定负责人必须存在且确为 owner 成员”。延迟检查允许同一事务先插项目，再插负责人成员，最后提交完整的一组数据。

### 9.2 用一次业务操作说明事务边界

**导入论文**：元数据 → 项目导入 → 处理任务 → 任务论文范围 → 幂等响应 → outbox，同一事务提交；任一环节明确失败则整体回滚。

**保存报告**：锁报告并比较基础版本 → 插入新版本正文 → 插入引用快照 → 更新当前版本指针 → 幂等响应 → outbox，同一事务提交。

PDF 文件保存由 B 的文件存储负责，不在数据库事务中；外部 AI 调用位于领取事务之后、完成事务之前，不持有长事务锁等待网络。

### 9.3 已有证据与本次工作区分

本次是文档梳理，核对了当前迁移、适配器及已有记录，**没有重跑数据库或业务测试**。以下是仓库已有的 2026-10-27 验证记录：

| 项目 | 已记录结果 | 依据 |
| --- | --- | --- |
| 共享契约与真实数据库门槛 | 41/41，含 6 个真实库门槛 | [适配器实现记录](../data-adapter.md) |
| K01—K15 及 C13 正反例探针 | 39/39；本次已核对 JSON 中 39 条 passed 均为 true | [约束记录](../day2-constraint-verification.json) |
| 真实库业务联调 | 9/9 | [适配器实现记录](../data-adapter.md) |
| outbox 投递器 | 5/5 | [适配器实现记录](../data-adapter.md) |
| 备份恢复 | 7/7 | [备份恢复记录](../backup-restore-verification.json) |

不能把“39 个正反例通过”说成“所有任意 SQL 写入路径、全部跨表不变量都已穷尽验证”。部分规则需要可信适配器配合，开发验收也不等于生产安全验收。

### 9.4 汇报中应明确的当前边界

- PostgreSQL 迁移、26 个 DataPort 方法、基础租约恢复、版本历史与 outbox 投递已有实现；早期设计文档中的“尚未建表”是当时状态，当前以迁移和实现记录为准。
- 向量模型/向量表已定义结构和维度约束；真实模型维度、全文与向量提交面仍需与 C 完成对接。当前 process 完成写入 `index_state='mock_only'`，不能称为真实向量检索全部验收。
- 作者与主题有关系结构，但当前 DataPort 没有对应真实写入参数，写入与统计语义仍需 A/C 对齐。
- 正式认证/持久化会话、正式库受限角色/RLS 与可信项目上下文需联合验收。复合 FK 约束关系归属，不能单独阻止越权 SELECT；触发器也不构成对数据库超级用户的防篡改边界。
- 租约心跳、最大重试/死信、幂等与 outbox 保留策略、未知提交的文件补偿对账仍属协作项。

## 10. 建议排成 10 页 PPT

| 页 | 标题 | 页面只放什么 | 口头重点 |
| --- | --- | --- | --- |
| 1 | 业务需求与数据库设计目标 | 一条业务链路、四条需求 | 数据库负责隔离、追溯、历史与一致性 |
| 2 | 局部 E-R 视图划分 | 四个视图的缩略图 | 为什么按这四块抽象 |
| 3 | 总 E-R 与核心联系 | 总图，突出 M:N、1:N、可选 1:1 各一例 | 18 个实体、28 个联系；最大基数与最小参与分开 |
| 4 | 三类冲突及消解 | 命名/属性/结构各一个案例 | 编号含义、引用作用域、历史快照 |
| 5 | E-R 到关系模式 | 四个转换例子 | 主键、外键、联系属性怎样落表 |
| 6 | 24 张表的分组 | 3/9/6/3/3 五组概览 | 不逐列念字段，完整模式留备查 |
| 7 | 完整性约束 | 三类基本完整性 + 负责人 SQL | 为什么 UNIQUE 不能单独保证“恰好一个” |
| 8 | 规范化与受控冗余 | role/author_order 依赖例子 + 三类保留重复 | 不笼统承诺全部表严格 3NF |
| 9 | 事务与并发冲突 | 导入、报告保存各一条流程 | 原子性、版本冲突、旧 worker 隔离 |
| 10 | 实现证据与协作边界 | 已有结果与三项待协作内容 | 测试记录的时间；真实模型与授权边界 |

若只有 5—6 分钟，把第 5、6 页合并，第 8 页放答辩备查，第 9、10 页合并。主讲保留实体与联系、三类冲突、关系转换、完整性四个核心部分。

## 11. 可直接使用的开场与收尾

**开场：**

> 我负责 PaperPilot 的数据库概念设计、关系模式和持久化实现。按照课件的方法，我先从项目协作、文献处理、研究报告和运行审计四个局部应用识别实体与联系，再集成为总 E-R 模型。在集成过程中统一编号、属性作用域和对象生命周期，之后把模型转换为 24 张关系表，并通过数据库约束和 Python 事务保证一致性。

**收尾：**

> 这个设计重点解决了三类数据问题：用项目范围和复合外键保证关联归属；用稳定片段与不可变版本快照保证来源可追溯；用任务尝试、幂等和事务保证重试与并发下的一致性。当前已有迁移、数据层和开发验证记录，真实模型提交与正式数据库授权仍需要联合验收。

## 12. 答辩速查

| 可能的问题 | 建议回答 |
| --- | --- |
| 为什么 role 不直接放 users？ | 它由用户与项目共同决定；用户在不同项目角色可以不同。 |
| 为什么作者顺序不放 authors？ | 作者顺序只在具体论文中有意义，依赖论文与作者的组合。 |
| 为什么 18 个实体却有 24 张表？ | M:N 联系转换为关联表，论文还拆成元数据与项目导入；概念实体与逻辑表不是一一计数。 |
| 论文和项目是不是 M:N？ | 如果“论文”指跨项目的抽象文献，可以这样建模；本项目公开 paper_id 表示一次项目导入，因此当前实际联系是项目 1:N 导入论文。 |
| 为什么一对一也拆表？ | 1:1 可合并但不是必须合并；元数据与导入、任务与结果各有不同字段、生命周期和约束。 |
| 为什么 citation_id 不是全局主键？ | 它在任务的引用 scope 或版本内解释，不同结果/证据字段可以重复出现相同编号。 |
| 任务一定有结果吗？ | 最多一个成功结果；成功任务必须有一个，未成功任务没有已提交成功结果。 |
| 为什么要另存引用快照？ | 需要保存当时的来源值，防止重处理或移出论文改变旧报告。 |
| 项目关联不变是否代表有权限？ | 不代表；复合 FK 保证归属正确，读取和提交仍重验当前项目成员资格，正式库授权另行实施。 |
| 是否所有表都满足 3NF？ | 核心依赖按规范化设计，但实现保留项目维度、指针及快照；应逐表判断，不能统一宣称严格 3NF。 |
| 幂等是不是用 request_id？ | 不是。幂等键是 user_id、project_id、operation、key；request_id 用于日志关联。 |
| 为什么审计与 outbox 不合成一张表？ | 审计事实应不可变，投递状态需更新与重试；分离后各自约束更清楚。 |

## 13. 核对来源

- 参考课件：`C:\Users\lsh15\Downloads\第五章 数据库设计.pdf`，重点页码见第 1 节。
- 业务接口与规则：[AB-1](../ab-data-contract.md)、[A 交接说明](../member-a-handoff.md)。
- 设计过程：[局部视图](01-requirements-and-local-er.md)、[合并对应表](02-merged-er.md)、[C01—C15 与 K01—K15](03-resolved-model.md)、[数据字典](04-data-dictionary.md)。
- 图形与联系：[总图](diagrams-chen-clean/total-er.svg)、[Chen 记法说明](06-chen-notation.md)、[绘图源](../../scripts/render_chen_clean.py)。
- 当前实现：[PostgreSQL 迁移](../../backend/app/data/migrations/001_initial.sql)、[PostgresData](../../backend/app/data/adapter.py)、[实现及历史验证记录](../data-adapter.md)。
- 本稿只新增汇报文档，不修改业务代码、数据库结构或既有契约。
