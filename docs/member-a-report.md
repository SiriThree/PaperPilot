# 成员 A 工作汇报（Phase 3 数据层）

日期：2026-10-27。范围：PaperPilot Secure 项目中成员 A 负责的数据库选型、关系模型、持久化、迁移、事务/约束/索引、并发恢复与性能实验。本文为独立汇报文档，各项证据均可在仓库中复跑；详细技术记录见 [data-adapter.md](data-adapter.md)。

## 一、成果概述

成员 A 独立完成了 PaperPilot 正式数据层从零到验收的全链路：**PostgreSQL 16 + pgvector 选型与预验证 → 24 张表关系模型设计 → 26 个 DataPort 方法的生产级实现 → 与 B 的业务层真实联调 → 事务性 outbox 投递器 → 约束/性能/备份恢复三类验收**。全部验收通过，共 9 个本地提交（已推送，PR #1），期间**测试当场发现并修复了 4 个真实工程缺陷**。

## 二、阶段成果

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| 1. 环境预验证 | fa1db36 | PostgreSQL 16.15 + pgvector 0.8.7 独立 Docker 容器（端口/密码随机化、镜像摘要固定）；四项实测通过：事务提交/回滚、向量列与余弦相似度、双角色行级隔离（RLS）、只读角色权限拒绝。产出可复跑脚本与机器可读结果 `day0-verification.json` |
| 2. 关系模型设计 | 0648bc1…0f22730 | 按标准流程完成：需求追溯（R01—R13）→ 4 张分 E-R 图 → 合并 → 冲突消解（C01—C15，区分契约要求/自身设计/待协作事项）→ **24 张表数据字典**（176 个字段、完整键约束、26 个方法到表的落点映射）；Chen 记法概念图 5 张，自研几何检查脚本保证图形可读；形成 K01—K15 跨行约束实现清单 |
| 3. 正式实现 | db9f5f9 | 24 表 DDL 迁移（复合外键、值域 CHECK、部分唯一索引、**延迟约束触发器**实现跨行规则）；26 个 DataPort 方法（psycopg3）；连接池/迁移器/readiness/错误映射；A 的可信组合入口；隔离库测试 provider |
| 4. 验证与联调 | 0d32642、808ced9、cdce9fd | 共享契约测试 41/41（6 个真实数据库门槛不再跳过）；业务联调 9/9；事务性 outbox 投递器及 5 项行为测试；性能基线实验；备份/恢复验收 7/7 |

## 三、关键设计与决策（附理由）

1. **公开编号与内部元数据分离（C01）**：`paper_id` 是项目导入身份，内部书目用 `metadata_id`，跨项目不共享内容，从结构上杜绝越权读取。
2. **跨行规则用延迟约束触发器而非应用层检查（K02/K04/K08—K11）**：如"项目恰好一位负责人且负责人必须是成员""每份证据恰好五个字段""报告版本必须连续且指针指向最新版"，由数据库在提交时强制，业务代码无法绕过。
3. **任务领取用 `FOR UPDATE SKIP LOCKED` + 租约**：多 worker 并发领取互不阻塞，租约过期恢复先封存旧尝试再递增尝试号，旧 worker 的迟到写入被 `task_id+attempts` 条件更新拒绝。
4. **并发导入的错误码按唯一约束名精确翻译**：同摘要并发上传的败者返回契约要求的 `DUPLICATE_PAPER`（409），而不是笼统的完整性冲突。
5. **提交结果三态映射**：明确回滚→`DATA_UNAVAILABLE`（可清理本次文件）；提交结果未知→`DATA_COMMIT_UNKNOWN`（禁止删除可能已采纳的文件，用原幂等键对账）。
6. **向量列采用不定维 `vector` + 版本化模型登记**：等待成员 C 确认真实模型维度期间不锁死列定义，模型变更新增记录而非原地改列。
7. **性能候选索引用实测否决而非照单全收**：在合成规模下三个候选索引均无计划级收益，故不落地，避免了无收益的写放大；审计深分页的瓶颈被定位为 OFFSET 算法成本，正确修法（游标分页）已记录为后续兼容扩展。

## 四、测试发现并修复的真实缺陷（亮点）

这些缺陷全部由自动化测试或验收脚本当场暴露，而非人工排查：

| # | 缺陷 | 危害 | 发现者 |
| --- | --- | --- | --- |
| 1 | 迁移器对已迁移的库误判"未识别数据库"而拒绝执行 | 服务第二次启动即失败 | 业务联调的重启用例 |
| 2 | 并发同摘要导入时败者错误码不符契约（笼统 409 而非 DUPLICATE_PAPER） | 前端无法区分"重复上传"与"编号占用" | 并发门槛测试 |
| 3 | outbox 触发器误写 `RETURN NULL`，导致 PostgreSQL **静默取消整行更新**且无任何报错 | 投递状态列永远无法更新，投递器完全失效 | 投递器测试 |
| 4 | 报告指针的延迟触发器读取事件时的行值而非提交时终态，同事务内"建报告→加版本→更新指针"被误判违规 | 正常保存报告流程失败 | K01—K15 约束探针 |

每个缺陷修复后都补充了对应的回归测试或探针（约束探针从 37 项扩至 39 项），防止复发。

## 五、验证与证据汇总（全部可复跑）

| 验证 | 结果 | 复跑方式 |
| --- | --- | --- |
| 共享契约测试（35 行为 + 6 真实库门槛） | **41/41，5 轮稳定** | `pytest backend/tests/data_contracts --data-contract-provider app.data.provider:build_harness -q` |
| K01—K15 + C13 约束正反例探针 | **39/39** | `python scripts/verify_constraints_day2.py` |
| persistent 业务联调（B 的真实路由 + worker） | **9/9** | `pytest backend/tests/test_persistent_integration.py -q` |
| outbox 投递器行为 | **5/5** | `pytest backend/tests/test_outbox_delivery.py -q` |
| 全量回归（默认 Mock 基线不破坏） | 172 passed / 37 skipped | `pytest -q` |
| 性能基线（A/B 计时 + EXPLAIN 证据） | 结论：现有索引足够，深分页需游标方案 | `python scripts/benchmark_postgres.py` |
| 备份/恢复验收（pg_dump → 销毁 → pg_restore → 逐项复验） | **7/7** | `python scripts/verify_backup_restore.py` |

安全边界说明：所有测试使用一次性数据库（用后即删），凭据只来自环境变量，代码与文档中无任何真实口令。

## 六、对其他成员的交付

- **对 B**：DataPort 适配器、`DataBinding` 组合入口（`a_mode=persistent` 一行命令启动）、迁移/初始化/关闭说明、隔离测试 provider、以及 B 的全部业务路由在真实库上的联调证据。B 无需改动任何 services/routes/前端代码即可切换持久层。
- **对 C**：`chunks.content` 完整正文列、版本化 embedding 模型登记与向量表结构已就绪，等待 C 确认真实模型维度后仅需一次列迁移。
- **对 D**：outbox 表 + 不可变触发器 + 可靠投递器（A/D 审计投递的对齐基础）、day0 已验证的 RLS/只读角色机制、19 张带项目边界的表为行级隔离预留了完整的项目维度。

## 七、当前状态与剩余工作

- **已推送**：PR #1（base: main ← member-a），包含全部 9 个提交，无冲突可合并。
- **A 独立工作已全部完成**。剩余为跨成员协作项：正式认证与会话（A/B/D）、RLS 与受限数据库角色接入正式库（A/D）、真实 embedding 维度与向量提交面（A/C）、租约心跳/幂等保留期/outbox 保留策略（A/B、A/D）。
- 每项剩余工作的前置结构均已就绪，协作者到位后只需对接，不需要返工。

## 附：环境与复跑

| 步骤 | 命令（PowerShell，仓库根目录） |
| --- | --- |
| 启动数据库 | `.\scripts\start-postgres-day0.ps1` |
| 迁移 + 种子 | `$env:PAPERPILOT_DATA_PASSWORD='…'; $env:PAPERPILOT_DATA_DBNAME='paperpilot'` → `.\.venv\Scripts\python.exe scripts\manage_postgres.py migrate`（seed-demo / check 同理） |
| persistent 启动 | `$env:PAPERPILOT_A_MODE='persistent'` → `.\.venv\Scripts\python.exe -m uvicorn app.data.binding:create_app --factory --app-dir backend` |
| 全部验收 | 见第五节表格 |

提交记录：`fa1db36`（环境）→ `0648bc1`/`eff6aca`/`e7482ed`/`0f22730`（模型）→ `db9f5f9`（实现）→ `0d32642`（联调）→ `808ced9`（投递器）→ `cdce9fd`（性能与备份）。
