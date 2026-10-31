# 成员 A：项目概览统计与数据展示（本轮交付）

对应阶段任务「数据统计展示（P0）」「数据库性能展示（P1）」与组长建议的"研究项目概览区域"。本文是给 B 的接入方案：A 提供数据结构、查询与组件建议，页面合入由 B 统一把关。**本轮未修改任何前端代码与 B 的服务。**

## 1 概览统计意图 `overview`（附加扩展，待 B/D 评审）

`PostgresData.controlled_statistics` 新增 `overview` 意图，返回项目范围内的 metric/value 长表：

```
columns = ["metric", "value"]
rows = [
  {"metric": "papers_total",         "value": 6},
  {"metric": "papers_searchable",    "value": 5},
  {"metric": "papers_processing",    "value": 0},
  {"metric": "papers_failed",        "value": 1},
  {"metric": "papers_pending",       "value": 0},
  {"metric": "chunks_total",         "value": 10},
  {"metric": "papers_with_evidence", "value": 2},
  {"metric": "reports",              "value": 1},
  {"metric": "report_versions",      "value": 2},
  {"metric": "members_total",        "value": 2},
]
```

语义与边界：

- 全部计数限定当前 `project_id`；论文相关计数只统计 `removed_at IS NULL` 的在库关联（移出论文不计入，见[生命周期](member-a-literature-ux.md)）。
- `chunks_total` 只计在库论文的片段；`papers_with_evidence` 是"至少有一份证据结果"的论文数。
- 年份分布沿用既有 `by_year` 意图（概览区把它作为第二张卡片的数据源），不在 overview 里重复返回。
- 长表（metric/value）而非宽表：新增指标不改变列结构，前端按 metric 取值渲染，未知 metric 忽略即可向后兼容。
- 这是 AB-1 三个冻结意图之外的**附加扩展**：`ab-data-contract.md` 原文"其他 UNSUPPORTED_INTENT"在本实现中仍对未知意图成立；`overview` 是否进入正式契约、HTTP 面如何暴露（建议 `POST /projects/{id}/secure-queries` 增加约定问题 → intent 映射，如"项目概览统计"），由 B/D 评审后决定。MockData（B 拥有）尚未实现该意图；B 若希望 Mock 模式下概览区也可演示，可按同一形状在 MockData 补齐（论文/片段计数可由 Mock 既有记录派生）。

## 2 查询与索引（P1 执行计划证据）

overview 为单条 SQL、10 个子查询，全部走既有索引，未新增索引：

| 子查询 | 计划 | 使用的索引 |
| --- | --- | --- |
| papers_total | Index Only Scan | `live_project_papers`（部分索引 `WHERE removed_at IS NULL`） |
| papers_searchable/processing/failed/pending | Index Scan + Filter | `live_project_papers` |
| chunks_total | Index Only Scan + Nested Loop | `chunks_project_id_paper_id_chunk_id_key` + `project_papers_paper_unique` |
| papers_with_evidence | Index Only Scan + Nested Loop | `evidence_by_paper`（部分索引 `WHERE kind='evidence'`）+ `project_papers_paper_unique` |
| reports / report_versions / members | 顺序点查 | 各表 project_id 前缀唯一键 |

种子库（6 论文/10 片段）实测 `EXPLAIN (ANALYZE, BUFFERS)`：**总执行 0.545 ms，32 buffers，全内存命中**。机器可读计划已在本轮验证中留存；命令：

```powershell
# 对任意已迁移库复测（把 <pid> 换成项目 ID）
psycopg: EXPLAIN (ANALYZE, BUFFERS) SELECT ...  # SQL 见 backend/app/data/adapter.py controlled_statistics("overview")
```

规模结论沿用[性能基线](performance-baseline.md)：该文档在 200 项目/4 000 论文/10 万审计的合成规模下验证了现有索引集合；overview 的所有子查询都落在该基线已覆盖的索引路径上，属于同量级点查，**不需要新索引**。若单项目论文/片段达到 10⁵ 量级（基线文档的重启评估条件），再复测。

## 3 已验证行为

`backend/tests/test_demo_seed_postgres.py`（真实库，无 day0 环境自动跳过）：

| 用例 | 验证 |
| --- | --- |
| test_seed_second_run_changes_no_rows | 种子重复执行 14 张表行数零变化；清单返回 already-* 状态 |
| test_seeded_content_readable_through_port | 6 论文（5 searchable + 1 failed）、每篇 2 片段、报告 2 版本（最新版 3 条引用） |
| test_overview_statistics_reflect_seed | overview 十项指标与 by_year 分布与种子内容完全一致 |

本轮对开发库（day0，`paperpilot`）的实际验证：连续 3 次 `seed-demo-project` 行数完全一致；后端进程重启后 alice 登录仍可见项目、6 篇论文与报告；`secure-queries` 的 by_year 返回 2021–2025 各 1 篇、2025 年 2 篇（与种子一致）。

## 4 给 B 的组件方案建议（项目概览区）

进入项目后的首屏（现"文献工作台"顶部区域）建议增加概览条，数据全部来自上述真实查询，不做前端硬编码：

1. **指标卡行**（overview 意图一次取全）：论文总数、可检索、处理失败、片段总数、成员数。失败数 > 0 时卡片高亮，并作为失败论文筛选的入口（联动文献列表 `status=failed`）。
2. **年份分布卡**（by_year 意图）：横向条形或迷你柱状；空项目显示明确空态，不渲染 0 轴假图。
3. **处理中/待处理提示**（overview）：`papers_processing + papers_pending > 0` 时显示"有 N 篇正在处理"的轮询提示，复用现有任务轮询机制（useTaskRecovery），不新增轮询通道。
4. **最新报告卡**（现有 reports API 首条）：版本号 + 更新时间，点击进报告页。
5. mock/synthetic 标记沿用现有约定：Mock 模式下概览数据标注"演示数据"，不声称真实库统计。

接入顺序建议：B 先在 MockSecurity/D 侧确定 overview 的暴露方式（或临时由前端聚合 papers API 的 status 字段——可演示但不满足"统计来自数据库聚合"的验收语义，仅作过渡）。

## 5 演示种子（配套交付）

- `python scripts/manage_postgres.py seed-demo-project`：在已迁移库灌入默认演示项目（与 Mock 演示同一套合成内容：6 论文、10 片段、2 证据、2 版报告、审计事件），**可重复执行**：幂等键 + 确定性 UUIDv5，重复运行返回原实体，零重复行。
- 项目行直接 SQL 引导（`create_project` 尚无可重放幂等键，见 scoped replay 候选说明）；论文/任务/证据/报告/审计全部走公开 DataPort 方法。
- 需在应用启动前执行：运行中的 worker 会领取 pending 演示任务并以 MockAI 内容完成，覆盖精选内容（脚本检测到非预期领取会明确报错）。
- `seed-demo`（开发用户）行为不变；`migrate`/`check` 不变。

## 6 未做与待协作

- 未改前端、未改 B 的 services/routes、未改 MockData、未改 OpenAPI/生成类型。
- overview 未接入 HTTP：等 B/D 确认暴露方式（网关意图映射或独立只读端点）。
- 概览自动刷新：建议 B 复用任务恢复轮询，不引入新推送通道。
