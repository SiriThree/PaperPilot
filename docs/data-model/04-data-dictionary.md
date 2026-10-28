# 关系模式与数据字典

本文件是冲突消解后的 PostgreSQL 16 逻辑模式，不是已执行的 DDL。共 24 张表，来源与决策见 [最终模型](03-resolved-model.md)。字段类型是迁移输入；C/D 待确认项仍按该文档列出的边界处理。

## 通用约定

- `PK` 主键、`UQ` 完整唯一键、`FK` 外键。表内“可空”仅指 SQL NULL，空字符串与空列表另按现有 Schema/AB-1 判断。所有 PK 字段 NOT NULL。
- `uuid` 使用 UUID 值；时间统一 `timestamptz`，数据库存储时刻，API 序列化带时区。`created_at` 默认 now()；`updated_at` 由写事务更新，不能靠只在插入时执行的 DEFAULT 保证。
- `hash64` 是 `varchar(64)`，附小写十六进制长度检查。`jsonb` 保留 Schema 形状；数组顺序、来源文字、synthetic/mock 标志不能丢失。
- 下文每张含 project_id 的表，除 audit_events/outbox_events 明确可空外均非空。每张这种表的 project_id 均 FK→projects(project_id)。列出的资源复合 FK 是额外约束，不能省略项目维度。
- 历史相关 FK 默认 `ON DELETE NO ACTION`、`ON UPDATE NO ACTION`。普通业务不物理删除项目/用户/导入/片段/结果/版本；成员删除是撤权，论文移出是软删除。
- 标为不可变的行只允许可信入口插入；后续迁移结合权限/触发器保护。K01—K15 是跨表/跨行规则编号，详见最终模型。

## 1 users

唯一业务用户来源，不另建 A 模块账户表。

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| user_id | uuid | 否 | PK；对外 User.user_id |
| username | varchar(64) | 否 | UQ，长度≥1；按原值精确匹配，不擅自改大小写语义 |
| display_name | text | 否 | 对外显示名；不额外设置公开契约没有的最大长度 |
| created_at | timestamptz | 否 | 初始化时间 |

真实密码摘要、禁用状态和会话结构待 A/B/D 冻结，本轮不把开发用户名登录当成正式认证。

## 2 projects

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | PK |
| name | varchar(100) | 否 | 1—100；不唯一 |
| description | varchar(1000) | 否 | 默认空字符串 |
| owner_id | uuid | 否 | FK→users(user_id)；首版不可变 |
| owner_role | text | 否 | 内部约束列，默认且 CHECK='owner'，不增加 HTTP 字段 |
| created_at | timestamptz | 否 | 创建时间 |

FK `(project_id,owner_id,owner_role)`→project_members(project_id,user_id,role)，`DEFERRABLE INITIALLY DEFERRED`。与成员同事务创建，提交时保证至少一个且正确的 owner，见 K02/K03。

## 3 project_members

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | PK 的第一部分，FK→projects |
| user_id | uuid | 否 | PK 的第二部分，FK→users |
| role | text | 否 | CHECK IN ('owner','member') |
| joined_at | timestamptz | 否 | 添加时间 |

PK `(project_id,user_id)`；UQ `(project_id,user_id,role)` 供负责人复合 FK 引用；部分唯一索引 `(project_id) WHERE role='owner'` 保证最多一位 owner。移除普通成员物理删除此行，历史表不引用这条关系。

## 4 papers

每次导入独立的书目元数据；metadata_id 从不作为公开 paper_id 返回。

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| metadata_id | uuid | 否 | PK |
| project_id | uuid | 否 | 所属项目，FK→projects |
| title | varchar(200) | 否 | 1—200，来自上传或可靠补录 |
| year | integer | 是 | NULL=未知；非空为 1000—3000 |
| created_at | timestamptz | 否 | 创建时间 |

UQ `(project_id,metadata_id)`。和 project_papers 提交后一对一；导入事务不能留下无关联元数据，K04 的反向存在性由延迟约束触发器验证。

## 5 project_papers

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| paper_id | uuid | 否 | PK，公开项目导入编号，来自 FileReference.paper_id |
| project_id | uuid | 否 | FK→projects |
| metadata_id | uuid | 否 | UQ；对应本次导入独占的书目元数据 |
| imported_by | uuid | 否 | FK→users，不引用当前成员关系 |
| digest | hash64 | 否 | B 计算的实际文件 SHA-256 |
| storage_key | varchar(1024) | 否 | B 保存的不透明相对文件键，遵循 validate_file，不是客户端绝对路径 |
| status | text | 否 | pending/processing/searchable/failed，默认 pending |
| index_state | text | 否 | pending/ready/failed/mock_only，默认 pending；内部索引完成标志 |
| active_process_task_id | uuid | 是 | 当前成功解析的 task_id，导入初期为空；当前片段集合从该结果的 root 来源读取 |
| removed_at | timestamptz | 是 | 逻辑移出时间；不清理历史来源 |
| created_at、updated_at | timestamptz | 否 | 导入及状态变更时间 |

UQ `(project_id,paper_id)`、`(project_id,digest)`、`metadata_id`。FK `(project_id,metadata_id)`→papers(project_id,metadata_id)；可空延迟 FK `(project_id,paper_id,active_process_task_id)`→task_results(project_id,paper_id,task_id)，提交前校验其 kind='process'，searchable 时必须有该指针。移出后仍受摘要唯一性限制；不对 digest 建“仅未移出”的部分唯一索引。正式 searchable 要求真实索引就绪；`mock_only` 明确只支持合成联调，不算真实检索验收。

## 6 authors

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| author_id | uuid | 否 | PK |
| project_id | uuid | 否 | FK→projects，首版不共享跨项目作者 |
| name | text | 否 | 非空；姓名不唯一，不以同名判断同一人 |

UQ `(project_id,author_id)`。真实作者录入/去重来源待 C 对齐，现有 DataPort 没有作者写入参数。

## 7 paper_authors

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | 项目边界 |
| paper_id、author_id | uuid | 否 | 复合 PK |
| author_order | integer | 否 | ≥1，同论文作者顺序不重复 |

PK `(paper_id,author_id)`；UQ `(paper_id,author_order)`。FK `(project_id,paper_id)`→project_papers；FK `(project_id,author_id)`→authors。作者顺序依赖这条联系，而不是 authors 本身。

## 8 topics

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| topic_id | uuid | 否 | PK |
| project_id | uuid | 否 | FK→projects |
| name | text | 否 | 非空主题名；默认精确匹配，不假定大小写归一化 |

UQ `(project_id,topic_id)`、`(project_id,name)`。主题归一化与多主题统计口径待 A/C 确认。

## 9 paper_topics

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | 项目边界 |
| paper_id、topic_id | uuid | 否 | 复合 PK，防重复分类 |

PK `(paper_id,topic_id)`。FK `(project_id,paper_id)`→project_papers；FK `(project_id,topic_id)`→topics。建议按主题各计一次，不直接把多主题数量求和当论文总数。

## 10 chunks

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| chunk_id | uuid | 否 | PK，全局唯一 |
| project_id、paper_id | uuid | 否 | 所属导入，复合 FK→project_papers |
| page_number | integer | 否 | ≥1；当前 Source 只提供单一定位页 |
| excerpt | varchar(4000) | 否 | 1—4000，公开 Source 摘录，保留原值 |
| content | text | 是 | 完整片段正文；现有 ProcessResult 未提供，待 C 通过私有提交面补足，不用 excerpt 冒充 |
| synthetic | boolean | 否 | 原文来源是否合成；不把合成片段改标真实 |
| created_at | timestamptz | 否 | 提交时间 |

UQ `(project_id,chunk_id)`、`(project_id,paper_id,chunk_id)`。片段在一次解析中的顺序及原始 citation_id 存在该 process 任务的 result_sources(root) 中，而不是片段的全局属性。已提交内容和归属不可原位修改；重新分片创建新编号；可复用同论文内容完全相同的旧片段，新编号及来源集合在同次完成事务提交。active_process_task_id 切换当前集合，历史快照仍引用保留的旧行。

## 11 embedding_models

以下为 A/C 协作结构提案，不表示已经选定模型或生成真实向量。

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| model_id | uuid | 否 | PK |
| project_id | uuid | 否 | FK→projects，避免暴露其他项目模型配置 |
| provider、model_name、revision | text | 否 | 稳定供应方/模型名/版本；不得存 API 密钥 |
| dimensions | integer | 否 | >0，真实值由 C 确认，不默认 3 |
| distance_metric | text | 否 | 初步采用 cosine；其他度量须与 C 对齐后变更 |
| created_at | timestamptz | 否 | 模型记录创建时间 |

UQ `(project_id,provider,model_name,revision)`、`(project_id,model_id,dimensions)`。维度、版本、度量不可原位改变；模型变化新增记录。

## 12 chunk_embeddings

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | 项目边界 |
| chunk_id、model_id | uuid | 否 | 复合 PK |
| dimensions | integer | 否 | 与模型维度相同 |
| embedding | vector | 否 | pgvector 实际向量；逻辑层允许不同模型版本的维度不同 |
| created_at | timestamptz | 否 | 实际向量生成时间 |

PK `(chunk_id,model_id)`；FK `(project_id,chunk_id)`→chunks；FK `(project_id,model_id,dimensions)`→embedding_models。CHECK `vector_dims(embedding)=dimensions`。采用不定维 vector 列的版本化提案，实际 ANN 索引需模型确定后按对应维度/度量设计，不承诺一条未定维索引覆盖所有模型。

## 13 tasks

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| task_id | uuid | 否 | PK，重试不换编号 |
| project_id | uuid | 否 | FK→projects |
| actor_id | uuid | 否 | 当前可信 Context.user_id，FK→users |
| request_id | uuid | 否 | 当前可信请求关联编号，不唯一 |
| kind | text | 否 | process/answer/evidence/draft |
| status | text | 否 | pending/processing/succeeded/failed |
| attempts | integer | 否 | 默认 0，≥0，仅领取递增；processing 时≥1 |
| payload | jsonb | 否 | normalize_payload 后的不可随意改写载荷；保存 QuestionRequest 与 FileReference 原形 |
| payload_fingerprint | hash64 | 否 | 对规范化载荷 fingerprint，不使用不稳定 repr |
| error_code、error_message | text | 是 | 仅失败时有一致的错误对；不含驱动细节或密钥 |
| created_at、updated_at | timestamptz | 否 | 创建和状态时间 |

UQ `(project_id,task_id)`、`(project_id,task_id,kind)`。不持久化可悬空的通用 result_ref 外键，成功结果从 task_results 派生。pending/processing/failed 无成功结果，succeeded 恰好一个结果，由 K08/K09 的事务与提交前约束验证。retry 更新 actor_id/request_id 并清错误，不修改规范化任务范围。

## 14 task_papers

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| project_id | uuid | 否 | 项目边界 |
| task_id、paper_id | uuid | 否 | 复合 PK，任务的显式论文范围快照 |

PK `(task_id,paper_id)`。FK `(project_id,task_id)`→tasks；FK `(project_id,paper_id)`→project_papers。按 UUID 去重排序后与 payload.paper_ids 一致；process/evidence 各恰好一篇，answer/draft 允许零篇。关联保留不代表移出后 task() 可继续读取。

## 15 task_attempts

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| task_id、attempt_no | uuid、integer | 否 | 复合 PK；attempt_no≥1 |
| project_id | uuid | 否 | 复合 FK 所需项目边界 |
| actor_id | uuid | 否 | 领取时可信用户，FK→users |
| request_id | uuid | 否 | 领取时请求编号；后续 retry 不改写旧尝试 |
| status | text | 否 | processing/succeeded/failed/expired，区别于任务 pending |
| claimed_at、lease_expires_at | timestamptz | 否 | 到期时间晚于领取时间；默认租约 120 秒由 DataSettings 提供 |
| finished_at | timestamptz | 是 | 非 processing 时填写，不早于领取 |
| error_code、error_message | text | 是 | 失败/过期原因，安全消息 |

FK `(project_id,task_id)`→tasks；UQ `(project_id,task_id,attempt_no)`。同任务至多一个 status='processing' 的部分唯一索引；恢复先将旧尝试标 expired 再增加尝试号。worker 提交必须与此行及 tasks 当前上下文一致。租约续期/最大重试待 A/B，未在此扩充接口。

## 16 task_results

四种任务结果共享一个稳定容器；“证据记录”由 kind='evidence' 的结果和五字段组成，不额外维护第二份证据主记录。

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| task_id | uuid | 否 | PK，FK 到同一任务；每任务最多一个成功结果 |
| project_id、kind | uuid、text | 否 | `(project_id,task_id,kind)` FK→tasks |
| committed_attempt | integer | 否 | 被接受的实际尝试号，≥1 |
| paper_id | uuid | 是 | process/evidence 必填；answer/draft 为空；项目复合 FK→project_papers |
| evidence_id | uuid | 是 | 仅 evidence 必填且全局 UQ，公开 EvidenceResult.evidence_id |
| payload | jsonb | 否 | normalize_result 后完整结果，保留正文、来源、citation_ids、insufficient_evidence、mock 等 |
| result_fingerprint | hash64 | 否 | 比较同次同结果重放 |
| created_at | timestamptz | 否 | 结果提交时间 |

UQ `(project_id,task_id)`、`(project_id,paper_id,task_id)`；FK `(project_id,task_id,committed_attempt)`→task_attempts(project_id,task_id,attempt_no)。kind/paper_id/evidence_id 的同行 CHECK 加 K09/K10 校验；结果仅对当前且已授权的 processing 尝试提交。process 的 result_ref.resource_id=paper_id，evidence=evidence_id，answer/draft=task_id。

## 17 evidence_fields

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| task_id、field_name | uuid、text | 否 | 复合 PK；字段名为 research_question/method/results/conclusion/limitations |
| project_id | uuid | 否 | 结果所属项目 |
| text | text | 是 | 公开 EvidenceField.text |
| status | text | 否 | not_mentioned/insufficient_evidence/pending_verification |

FK `(project_id,task_id)`→task_results；UQ `(project_id,task_id,field_name)`。K10 延迟验证父结果 kind='evidence' 且恰好五行；pending_verification 必须有非空文本和支撑来源。其他状态不擅自增加公开契约没有的文本可空性限制。

## 18 result_sources

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| task_id、scope、citation_id | uuid、text、varchar(32) | 否 | 复合 PK；编号长 1—32 |
| project_id | uuid | 否 | 项目边界 |
| field_name | text | 是 | 证据来源所属字段；根来源为空 |
| ordinal | integer | 否 | ≥1，保留该来源列表原始顺序 |
| paper_id、chunk_id | uuid | 否 | 引用实际项目论文和片段 |
| page_number | integer | 否 | ≥1，冻结的来源位置 |
| excerpt | varchar(4000) | 否 | 1—4000，冻结的摘录 |
| synthetic | boolean | 否 | 原来源标志 |

UQ `(task_id,scope,ordinal)`。FK `(project_id,task_id)`→task_results；FK `(project_id,paper_id,chunk_id)`→chunks；可空 FK `(project_id,task_id,field_name)`→evidence_fields，根来源按 MATCH SIMPLE 跳过此 FK。

scope='root' 当且仅当 field_name=NULL；否则 scope=field_name。证据仅使用五字段 scope，其他结果仅用 root；父 kind 与 scope 的跨行匹配归 K10。每字段独立编号，禁止建立跨所有证据字段的 UNIQUE(task_id,citation_id)。payload 中来源与此表同事务写入并交叉校验，不能各自更新。

## 19 reports

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| report_id | uuid | 否 | PK，报告实体稳定编号 |
| project_id | uuid | 否 | FK→projects |
| created_by | uuid | 否 | FK→users |
| current_version_id | uuid | 否 | 最新已提交版本指针，用于锁行/CAS |
| created_at | timestamptz | 否 | 首次保存时间 |

UQ `(project_id,report_id)`。延迟 FK `(project_id,report_id,current_version_id)`→report_versions(project_id,report_id,version_id)，保证报告提交后至少有一版且指针同项目、同报告。

## 20 report_versions

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| version_id | uuid | 否 | PK |
| project_id、report_id | uuid | 否 | 复合 FK→reports |
| number | integer | 否 | 从 1 连续递增 |
| base_version_id | uuid | 是 | 首版为空，后续版本指向同报告前一版 |
| title | varchar(200) | 否 | 1—200，属于版本而不是可变报告标题 |
| research_question | varchar(2000) | 否 | 1—2000 |
| body | text | 否 | 1—100000，保存后不可覆盖 |
| created_by | uuid | 否 | FK→users，保留历史保存者 |
| created_at | timestamptz | 否 | 版本保存时间 |

UQ `(report_id,number)`、`(project_id,version_id)`、`(project_id,report_id,version_id)`；非空 base_version_id 上部分 UQ `(report_id,base_version_id)`。复合自 FK `(project_id,report_id,base_version_id)`→本表对应候选键，NULL 首版跳过。CHECK 首版 number=1/base=NULL、其他 number>1/base 非空；K11 延迟验证前版号+1及 reports 指向最大版本，正文/关联行禁止更新删除。

## 21 report_version_sources

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| version_id、citation_id | uuid、varchar(32) | 否 | 复合 PK，编号长 1—32 |
| project_id | uuid | 否 | 项目边界 |
| ordinal | integer | 否 | ≥1，保留版本 sources 顺序 |
| paper_id、chunk_id | uuid | 否 | 原来源定位键 |
| page_number | integer | 否 | ≥1，保存时的页码 |
| excerpt | varchar(4000) | 否 | 1—4000，保存时的原文摘录 |
| synthetic | boolean | 否 | 原来源标记 |

FK `(project_id,version_id)`→report_versions；FK `(project_id,paper_id,chunk_id)`→chunks；UQ `(version_id,ordinal)`。每版 0—100 条来源，对应现有 ReportSave 允许空来源；历史读取直接读快照，移出论文不会改写这些值。新保存必须重新验证当前论文可访问。

## 22 idempotency_records

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| user_id、project_id、operation、key | uuid、uuid、text、varchar(128) | 否 | 复合 PK；operation 为 upload/process/answer/evidence/draft/report-save；key 匹配现有规则 |
| request_fingerprint | hash64 | 否 | 规范化业务参数哈希；上传忽略新暂存键和新随机 paper_id |
| response_snapshot | jsonb | 否 | 原始成功响应，不以新状态替换旧响应快照 |
| paper_id、task_id、version_id | uuid | 是 | 与操作相符的目标键；不是无类型 resource_id |
| created_at | timestamptz | 否 | 首次确认成功时间 |
| expires_at | timestamptz | 是 | 演示期始终 NULL，无自动清理 |

user_id FK→users，project_id FK→projects；可空复合 FK `(project_id,paper_id)`→project_papers、`(project_id,task_id)`→tasks、`(project_id,version_id)`→report_versions。upload 要求 paper/task，任务操作要求 task，report-save 要求 version；其他目标列为空。request_id 不作为键；create_project 不写这张表冒充幂等。外层 upload 和内部 process 可有各自幂等记录，同事务保存。

## 23 audit_events

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| event_id | uuid | 否 | PK |
| timestamp | timestamptz | 否 | 公开事件发生时间 |
| request_id | uuid | 否 | 关联编号，可有多个不同事件 |
| actor_id、project_id | uuid | 是 | FK→users/projects；登录可空 |
| action、resource_type、decision、execution_status | text | 否 | 保持现有 AuditEvent 的字符串语义，不擅自改枚举 |
| policy_id | text | 是 | 安全策略编号 |
| duration_ms | double precision | 否 | ≥0且有限，不保存 NaN/Infinity |
| mock | boolean | 否 | 保留事件来源，不把 Mock 改标真实 |
| event_fingerprint | hash64 | 否 | 按完整规范化 AuditEvent 比较 event_id 重放 |

仅可信审计入口 INSERT；普通业务角色不得 UPDATE/DELETE/TRUNCATE。actor/project 的 NULL 不表示普通用户可以读取全局审计。AuditEvent 当前没有 resource_id/SQL 正文列，本设计不擅自扩展公开审计 Schema。事件指纹用于重复检测，不等于对数据库管理员的密码学防篡改保证。

## 24 outbox_events

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| event_id | uuid | 否 | PK，投递目标 audit 的事件编号 |
| project_id、actor_id | uuid | 是 | FK→projects/users，业务事件一般非空 |
| request_id | uuid | 否 | 业务请求关联编号 |
| payload | jsonb | 否 | 最小领域 AuditEvent 载荷；不包含报告正文/论文正文/凭据 |
| created_at | timestamptz | 否 | 与业务提交同事务产生 |
| delivery_attempts | integer | 否 | ≥0，默认 0 |
| next_attempt_at | timestamptz | 否 | 待重投时间，默认 now() |
| published_at | timestamptz | 是 | 确认投递后填写 |
| last_error_code | text | 是 | 脱敏错误码 |

event_id 与 audit_events 逻辑对应，不强制 FK，允许未投递。payload 及关联身份不可修改，投递账户只可改投递状态列；重试使用相同 event_id。可靠投递进程、确认和保留周期待 A/D；本表不替代 D 的执行前审计失败拒绝规则。

## 25 DataPort 全方法映射

| 方法 | 主要关系表 | 关键规则 |
| --- | --- | --- |
| user_by_name、user_by_id | users | 缺失返回 None；仅可信身份入口 |
| role、projects | users、project_members、projects | 最新成员关系，不以缓存权限替代 |
| create_project | projects、project_members、outbox_events | K02；负责人关系同事务 |
| members、add_member、remove_member | users、projects、project_members、outbox_events | owner 限制、成员唯一、负责人不可移除 |
| papers、paper | project_papers、papers | 项目、removed_at、年份过滤 |
| sources | project_papers、task_results、result_sources、chunks | 只读取 active_process_task_id 的 root 来源，保留原始编号、顺序及 synthetic |
| evidence | project_papers、task_results、evidence_fields、result_sources | 返回所有已提交 evidence 结果，按字段恢复来源 |
| remove_paper | project_papers、outbox_events | 只更新 removed_at，保留历史 |
| import_paper | papers、project_papers、tasks、task_papers、idempotency_records、outbox_events | 同摘要唯一和导入原子性 |
| create_task | tasks、task_papers、idempotency_records、outbox_events | kind/载荷与规范化指纹 |
| task、retry_task | tasks、task_papers、task_attempts、task_results、result_sources、project_papers、outbox_events | 任务读取重授权；重试不新建任务 |
| claim_job | tasks、task_attempts、task_papers、project_papers、outbox_events | 原子领取和尝试号递增 |
| complete_job、fail_job | tasks、task_attempts、task_papers、task_results、chunks、evidence_fields、result_sources、project_papers、outbox_events | 当前尝试、结果验证、原子成功/失败 |
| save_report | reports、report_versions、report_version_sources、chunks、project_papers、idempotency_records、outbox_events | CAS、引用再验证、不可变快照 |
| versions、reports | reports、report_versions、report_version_sources | 按项目读授权历史，版本 number 升序/最新指针 |
| controlled_statistics | project_papers、papers、paper_topics、topics、task_results | by_year/by_topic/missing_evidence；后者无 evidence 结果，而不是仅查是否有待处理任务 |
| append_audit、audit | audit_events、projects、project_members、users | 唯一事件追加；负责人项目筛选与稳定倒序分页 |

上表覆盖全部 26 个方法，多个方法共享同一组表，并不代表新增接口。向量、作者、主题的真实写入面不在当前 26 个签名中，需 A/C 私有适配或明确兼容扩展，不能未经协作改签名。
