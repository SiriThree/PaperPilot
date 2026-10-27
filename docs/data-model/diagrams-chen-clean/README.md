# 概念版 Chen E-R 图（5 张）

本目录是原 `docs/data-model/diagrams/` 的**独立重绘版**，不覆盖原有 4 张分图、合并总图和最终总图。图形约定与参考样式一致：

- 矩形：实体
- 菱形：联系（含一个自联系“基于”）
- 椭圆：属性；**实下划线**表示主键，**虚下划线**表示部分键（须与所属实体的主键组合才唯一，按弱实体的意思理解，但不画双线框）
- 连线上的 `1 / N / M`：最大基数（全部图统一只标最大值，不再混用 `0..1`）
- 联系自己的属性（如“项目角色”“作者顺序”“向量值”）画在菱形旁边
- 外键一律画成联系，不再作为实体属性出现

| 文件 | 内容 |
| --- | --- |
| [v1-identity-project.svg](v1-identity-project.svg) | 身份与项目：用户、研究项目、“参与”联系 |
| [v2-literature-source.svg](v2-literature-source.svg) | 文献与来源：用户、项目、论文、作者、主题、片段、向量模型 |
| [v3-research-report.svg](v3-research-report.svg) | 研究与报告：用户、任务、结果、证据、结果来源、报告版本及版本快照 |
| [v4-control-audit.svg](v4-control-audit.svg) | 运行控制与审计：幂等、审计、outbox、执行尝试、被采纳的结果 |
| [total-er.svg](total-er.svg) | 概念总图：18 个概念实体、28 个联系 |

## 合并掉的重复结构

这一版直接把“关联表”还原成 Chen 记法的菱形联系，不再把关联表同时画成实体。主要调整如下。

| 原图 | 新图处理 | 原因 |
| --- | --- | --- |
| V1：`USER —参与— MEMBERSHIP —包含— PROJECT`，再加 `USER —负责— PROJECT` | 改为 `USER —参与— PROJECT`，`role`、`joined_at` 作为联系属性 | 成员关系就是用户与项目的 M:N；`负责`只是 `role = owner`，再画一条联系会形成两套负责人事实 |
| V2：`PAPER` 与 `PROJECT_PAPER` 两实体、`纳入/导入` 两条联系 | 合并为“论文”实体，`PROJECT —导入— PAPER` 1:N | 当前设计中每次导入独占一份论文元数据，`paper_id ↔ metadata_id` 为 1:1，概念层不需要两个实体 |
| V2：`EMBEDDING` 实体，`CHUNK —向量表示— EMBEDDING —生成向量— MODEL` | 改为 `CHUNK —向量化— EMBEDDING_MODEL` M:N，`embedding`、`created_at` 作为联系属性 | 片段向量本质是片段与模型的关联行；维度由模型统一约束（K15） |
| V2/V3：`PAPER_AUTHOR`、`PAPER_TOPIC`、`TASK_PAPER` 关联实体 | 分别折叠为 `撰写`、`归类`、`选定范围` 菱形联系 | 关联表没有独立生命周期；作者顺序等作为联系属性 |
| V3：一个 `SOURCE` 同时连接结果、证据字段、片段和报告版本 | 拆成 `RESULT_SOURCE`（结果来源）与 `VERSION_SOURCE`（版本引用快照） | 实时引用与历史快照生命周期不同，C06 已要求分开保存 |
| V3/总图：`REPORT_VERSION —保存版本— REPORT` 之外再画 `current_version` 指针 | 只保留 1:N 保存版本联系，`current_version_id` 作为实现指针说明 | 当前版本指针可由版本集合派生，再画一条联系会重复表达 |
| V4：`OUTBOX_EVENT —投递为— AUDIT_EVENT` | 删除该菱形 | 文档已说明两者只按 `event_id` 幂等关联，不是强制外键；审计也可独立追加 |
| V4/总图：outbox 的发起人、幂等记录的任务/版本重放等辅助外键 | `outbox.actor_id` 作为可选审计维度、重放外键都不画 | 避免为可选审计维度和重放指针重复画联系；完整复合外键仍以数据字典为准 |

## 2026-10-27 规范修订

按 Chen 记法检查后的修改：

| 问题 | 修改 |
| --- | --- |
| 幂等记录没有任何主键标记 | `operation`、`key` 标为部分键；与“提交请求”（用户）、“幂等归属”（项目）组合成 PK `(user_id, project_id, operation, key)` |
| 依附实体只标了局部编号，读起来像全局唯一 | 执行尝试 `attempt_no`、证据字段 `field_name`、结果来源 `scope + citation_id`、版本引用快照 `citation_id` 改为虚下划线部分键；结果来源原先漏标的 `scope` 已补上 |
| 外键被画成属性 | 论文 `imported_by` → “用户 —上传— 论文”；任务 `actor_id` → “用户 —发起— 处理任务”；版本 `base_version_id` → “报告版本 —基于— 报告版本”自联系（1:1，K11）；结果 `committed_attempt` → “执行尝试 —采纳— 研究结果”1:1；版本引用快照删除与“快照定位”联系重复的 `chunk_id` 属性 |
| 总图丢失“谁做的” | 总图补上“上传”“发起”“保存（report_versions.created_by）”三条用户联系；`reports.created_by` 等于首版保存者，不另画 |
| 缺核心业务属性 | 报告版本补“正文 body”“研究问题 research_question”；审计事件补“发生时间 timestamp”“请求编号 request_id” |
| 基数写法混用 `0..1` | 全部改为只标最大基数 `1 / N / M`，可空和最小参与见数据字典 K01—K15 |
| 命名不一致或重名 | V3 的 `RESULT` → `TASK_RESULT`，V4 的 `IDEMPOTENCY` → `IDEMPOTENCY_RECORD`；3 个“限定项目”分别改为“幂等归属/审计归属/事件归属”，2 个“包含”改为“管理报告/切分”，第二个“定位”改为“快照定位” |

研究结果与处理任务是 1:1，直接共用 `task_id` 作主键，仍用实下划线，并在图注中说明。

### 总图唯一的一处交叉

加上三条用户联系后，总图在数学上不能画成没有交叉的平面图：用户、研究项目、原文片段三者都分别连到论文、处理任务、报告版本（联系路径可以经过中间实体），这就构成 K3,3。因此总图保留**一处**不可避免的交叉，即“保存”与“导入”，并在“保存”线上画跨线弧，表示两条线不相交。生成脚本只对标了 `hop=True` 的联系放行这一类交叉，其余交叉、穿形和重叠仍按原规则检查。

## 总图与 24 张逻辑表的关系

`total-er.svg` 是**概念总览**，不是 24 张表的字段级图。它把 `project_members`、`papers/project_papers`、`paper_authors`、`paper_topics`、`task_papers`、`chunk_embeddings`、`result_sources/report_version_sources` 等关联结构折叠为联系或独立来源实体，因此只保留 18 个概念实体和关键属性。

字段级完整结构仍以：

- 原 [resolved-er.svg](../diagrams/resolved-er.svg)；
- [数据字典](../04-data-dictionary.md) 中的 24 张逻辑表和 176 个字段；

为准。总图只用于快速阅读实体、联系和基数，不替代建表依据。

## 布局与重新生成

5 张图采用手工坐标布局，生成脚本会检查：

- 关系线与属性连线之间的交叉（只放行上文说明的 `hop` 交叉）；
- 连线穿过其他实体、菱形或椭圆；
- 形状/椭圆包围盒重叠；
- 文字是否超出椭圆或实体。

重新生成：

```powershell
python scripts/render_chen_clean.py --check
```

脚本只使用 Python 标准库，输出 SVG；不会修改原 `diagrams/` 目录。PNG 预览可由浏览器打开 SVG 后截图，不纳入版本库。

## 阅读时的注意点

1. 图中 `1/N/M` 只表示最大基数；最小参与数量、项目边界、证据五字段、每项目一名 owner、审计/outbox 的可空项目与操作者等约束以数据字典 K01—K15 为准。
2. 分图仍保留局部阶段的核心业务属性，不是全部字段；完整字段见数据字典。作者、主题、向量模型在字典中都带非空 `project_id`（按项目隔离），图中不重复画这些辅助外键。
3. 概念版分图已经吸收了冲突消解的结论（例如分开 SOURCE、合并 PAPER），与 [01 需求分析](../01-requirements-and-local-er.md) 中“局部图保留歧义”的写法不同。说明“分图 → 合并 → 消解冲突”的过程时，仍以原 `diagrams/` 下的分图和合并图为准。
4. 本项目当前仍是设计阶段，图上的联系不代表数据库约束、RLS 或事务已经实现。
