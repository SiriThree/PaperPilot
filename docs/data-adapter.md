# 成员 A 正式数据适配器实现记录（2026-10-27）

本文件记录 PostgreSQL 16 + pgvector 正式 DataPort 的落地结果、使用方式与验证证据。设计依据为 [数据字典](data-model/04-data-dictionary.md) 与 [最终模型](data-model/03-resolved-model.md)；接口语义以 [AB-1](ab-data-contract.md) 为准，未做任何修改。基准为 member-a 分支 `0f22730` 之上的本地提交（尚未推送）。

## 1 交付内容

| 位置 | 内容 |
| --- | --- |
| `backend/app/data/migrations/001_initial.sql` | 24 张表 DDL：主键/唯一键/复合外键、CHECK 值域、部分唯一索引、延迟约束触发器（K02/K04/K08—K11）、不可变保护触发器（chunks、report_versions、report_version_sources、audit_events、outbox 身份列） |
| `backend/app/data/database.py` | `ConnectionConfig`（显式凭据，无默认密码）、`PostgresDatabase`（小型固定连接池、单连接单事务、错误映射、迁移器、readiness、开发用户种子） |
| `backend/app/data/adapter.py` | `PostgresData`：26 个 DataPort 方法（psycopg3），行为对齐 AB-1 与既有共享测试 |
| `backend/app/data/mapping.py` | 行 → 公开模型映射；成功结果由 `task_results` 派生，不持久化可悬空 result_ref |
| `backend/app/data/binding.py` | 可信组合入口：`compose_data_binding()` 构造 `DataBinding(data, ready, close)`；`create_app()` 交 B 的工厂以 `a_mode=persistent` 启动 |
| `backend/app/data/deliverer.py` | `OutboxDeliverer`：原子领取、限时租约和尝试次数 fencing；指数退避、超限死信与 `published_at` 确认。审计按 event_id 去重，投递为至少一次，不能外推为其他下游副作用恰好一次；独立 CLI 读取 `PAPERPILOT_DATA_*`。 |
| `backend/app/data/provider.py` | 隔离测试 provider `app.data.provider:build_harness`：每 harness 建一个一次性数据库，含真实提交前故障探针、reopen、expire_claim；`disposable_database()` 辅助供其他真实库测试复用 |
| `backend/tests/test_outbox_delivery.py` | 投递器行为测试：恰好一次审计效果、丢失确认后的仅确认重放、永久失败退避（载荷非法记 `ADAPTER_CONTRACT_ERROR`）、并发投递器、独立构造器 |
| `backend/tests/test_persistent_integration.py` | persistent 模式业务联调：B 的真实路由 + worker 跑在真实 DataPort 上（登录、上传幂等重放、任务处理、问答/证据、检索、报告版本与冲突、成员/审计、移出保留、HTTP→outbox→投递器→audit 全链路、跨进程重启保留）；无库环境自动整体跳过，不影响 Mock 基线 |
| `scripts/manage_postgres.py` | 显式 `migrate / seed-demo / check` 命令，读取 `PAPERPILOT_DATA_*` 环境变量 |
| `scripts/verify_constraints_day2.py` | K01—K15 正反例探针（37 项），产出 `docs/day2-constraint-verification.json` |

原有 `backend/app/persistence/`（SQLite 历史实验）与 Mock 默认启动保持原状；本次未修改 B 的 services/routes/前端，未改任何公开 Schema。

## 2 配置与运行

数据层凭据只来自环境变量，代码无默认密码；缺失即拒绝启动：

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `PAPERPILOT_DATA_HOST` / `PAPERPILOT_DATA_PORT` | PostgreSQL 地址 | `127.0.0.1` / `54326` |
| `PAPERPILOT_DATA_USER` | 登录角色 | `postgres` |
| `PAPERPILOT_DATA_PASSWORD` | 密码（必填，可为空即拒绝） | 无 |
| `PAPERPILOT_DATA_DBNAME` | 业务库名（必填） | 无 |
| `PAPERPILOT_LEASE_SECONDS` | 租约秒数（1—3600） | `120`（`DataSettings`） |

PowerShell（仓库根目录，先 `scripts/start-postgres-day0.ps1` 启动容器）：

```powershell
$env:PAPERPILOT_DATA_PASSWORD='<.runtime/day0.env 中的口令>'; $env:PAPERPILOT_DATA_DBNAME='paperpilot'
.\.venv\Scripts\python.exe scripts/manage_postgres.py migrate      # 建表（拒绝未识别的非空库）
.\.venv\Scripts\python.exe scripts/manage_postgres.py seed-demo    # alice/bob/eve 开发身份（幂等）
.\.venv\Scripts\python.exe scripts/manage_postgres.py check
$env:PAPERPILOT_A_MODE='persistent'
.\.venv\Scripts\python.exe -m uvicorn app.data.binding:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

迁移不随 readiness 自动执行；readiness 只校验 `schema_migrations` 版本。生产环境配置仍被 B 的工厂明确拒绝。

## 3 关键实现决策

- **迁移幂等**：迁移器对已迁移的库只核对版本与校验和后跳过，重复执行安全；仅对"有业务表但没有 schema_migrations"的外来库拒绝（该缺陷由业务联调的重启用例发现并修复）。
- **事务边界**：每次调用独立借还连接、独立事务；写方法在返回前执行提交前探针（仅供测试注入）。提交失败的 `IntegrityError`（含延迟约束触发器在 COMMIT 抛出的错误）映射 `IDENTIFIER_CONFLICT`，其余已知回滚映射 `DATA_UNAVAILABLE`，无法确认回滚的提交故障映射 `DATA_COMMIT_UNKNOWN`。AppError 一律回滚后原样抛出。
- **导入并发**：同摘要并发导入时预检查可能同时通过，立即唯一键在 INSERT 处裁决；按约束名翻译，`(project_id,digest)` 冲突返回 `DUPLICATE_PAPER`，其余返回 `IDENTIFIER_CONFLICT`（契约语义，而非笼统 409）。
- **任务领取**：`SELECT … FOR UPDATE SKIP LOCKED` 保证多 worker 只有一个领取者；租约过期恢复先将旧尝试标 `expired`（部分唯一索引保证同任务至多一个在途尝试），再递增 `attempts` 并新建尝试行。
- **结果派生**：`tasks` 不存结果 JSON；`task()` 的 `result/result_ref` 从 `task_results` 及其类型化行（evidence_fields/result_sources）按项目重新装配，evidence 来源按 scope 内 ordinal 保序。
- **searchable 边界**：`complete_job(process)` 置 `status='searchable'` 同时写 `index_state='mock_only'`——这是合成联调标志，真实索引完成语义仍待 C；约束触发器保证 searchable 必有 active process 指针。
- **outbox**：业务写事务同事务写最小领域事件（动作、资源、决策、状态），`append_audit` 本身不再写 outbox。投递器独立进程重试，语义为投递 at-least-once、审计效果 exactly-once（event_id 主键去重）；投递列之外的行更新被触发器拒绝（见下条）。**保留周期与死信仍待 A/D**：published 行无限保留，业务入口禁止删除 outbox。
- **BEFORE 触发器必须 RETURN NEW**：`protect_outbox_identity` 最初误写 `RETURN NULL`，导致包括投递列在内的所有 outbox 更新被 PostgreSQL 静默取消（rowcount 0、无报错）——由投递器测试发现；已修复并在 K01—K15 探针中补上 C13 正反例（投递列可更新、身份与载荷不可更新）。
- **租约模拟**：测试 provider 的 `expire_claim` 同时回拨 `claimed_at` 与 `lease_expires_at`，保持 `lease_expires_at > claimed_at` CHECK 成立。
- **延迟触发器读终态**：K11 报告指针触发器在提交时重读行终态而非事件时的 NEW，否则同事务"插报告→加版本→更新指针"会被过期的 INSERT 事件误判。

## 4 验证证据（2026-10-27，本机）

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 共享行为 + 真实库门槛 | `pytest backend/tests/data_contracts --data-contract-provider app.data.provider:build_harness -q --basetemp .runtime/pytest-local` | **41/41 通过（35 行为 + 6 数据库门槛不再 skip），连续 5 轮稳定** |
| persistent 业务联调 | `pytest backend/tests/test_persistent_integration.py -q --basetemp .runtime/pytest-local` | **9/9 通过**：B 的真实路由 + worker + 真实库全链路（含 outbox 投递链路）；PG 不可达时整模块 skip，Mock 基线不受影响 |
| outbox 投递器 | `pytest backend/tests/test_outbox_delivery.py -q --basetemp .runtime/pytest-local` | **5/5 通过**（恰好一次、丢失确认、退避、并发） |
| K01—K15 + C13 约束正反例 | `python scripts/verify_constraints_day2.py` | **39/39 通过**，机器可读结果见 [day2-constraint-verification.json](day2-constraint-verification.json) |
| 性能基线（A/B + EXPLAIN） | `python scripts/benchmark_postgres.py` | 候选索引在合成规模（200 项目/4 千论文/10 万审计）下无计划级收益，**本轮不落地索引**；深分页瓶颈为 OFFSET 算法成本，结论与重启评估条件见 [performance-baseline.md](performance-baseline.md) |
| 备份/恢复验收 | `python scripts/verify_backup_restore.py` | **7/7 通过**：pg_dump（容器内自定义格式）→ 销毁库 → pg_restore → 业务快照逐字段一致、行数一致、幂等重放有效、outbox 可继续投递，见 [backup-restore-verification.json](backup-restore-verification.json) |
| 全量回归 | `pytest -q --basetemp .runtime/pytest-local` | 172 passed（158 基线 + 8 业务联调 + 5 投递器 + 1 全链路），37 skipped（历史 SQLite 实验未显式运行） |
| persistent 组合冒烟 | 一次性数据库 + TestClient | `/health` a=persistent；alice 登录；建项目真实落库（`mock=false`）；关闭后一次性库已删除 |

所有一次性数据库（`paperpilot_contract_*`、`paperpilot_constraints_*`）由 provider/脚本自动 `DROP … WITH (FORCE)`，验证后无残留；预验证库 `paperpilot_day0_preflight` 未被业务使用。本机系统 Temp 的 pytest 权限异常沿用仓库内 `--basetemp .runtime/pytest-local`。

## 5 尚未完成（不阻塞 AB-1 验收，列入 A/D、A/C、A/B 协作）

1. outbox 保留周期与死信（投递器已实现；published 行无限保留，删除被业务入口触发器拒绝，策略待 A/D）；D 的执行前关键审计不依赖本表。
2. 正式认证、密码摘要、会话持久化、账户禁用；当前仍为开发身份。
3. 数据库受限角色、受控视图、RLS 与可信项目上下文（day0 已预验证机制可行，未接入正式库授权）；`forbid_mutation` 触发器只是业务入口约束，超级用户可绕过，不构成防篡改边界。
4. C 的真实模型维度、正文/向量私有提交面；`EMBEDDING_DIM=3` 仍为合成占位，`chunk_embeddings` 采用不定维 vector + 版本化模型登记方案。
5. 租约心跳、最大重试、死信；幂等记录演示期无限保留（`expires_at` 由 CHECK 固定为 NULL）。
6. 文件补偿对账与生产容量验收：索引候选经实测**否决**（重启评估条件见[性能基线](performance-baseline.md)）；审计深分页的游标升级属 AB-1 兼容扩展，待产品需要时另行立项。备份/恢复已验收（见验证表）。
