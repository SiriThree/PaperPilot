# Phase 2B 基础持久化（临时 SQLite）

> 历史实验记录，已由用户确认的“数据库选型中立”调整取代。当前 B 工厂不再自动注册 SQLite、迁移或初始化；下文原 persistent 启动命令不再直接可用。已有模型/迁移文件保留未改，供参考。当前有效入口、测试命令和责任边界见 [中立接入说明](persistence-integration.md)。本页过去的选择不代表团队正式决策。

## 定位与选择依据

仓库 README 与开发指南把 PostgreSQL/pgvector 写作 A 阶段预期，没有已落地的 PostgreSQL 连接配置、驱动、ORM、迁移或团队正式选型记录。因此本阶段选择 Python 标准库 sqlite3，先实现 AB-1 的真实持久化，不增加依赖或第二套数据库实现。后续正式数据库仍由 A 与团队确认。

此实现用于本机前端和业务 API 联调：`a=persistent,c=mock,d=mock`。真实保存业务数据，AI 内容仍为合成数据，统计入口仍经过 Mock SecurityPort。`production` 禁止临时 SQLite；`real` 仍拒绝启动。SQLite 文件不提供 D 所需的数据库角色、RLS 或受限 SQL 执行身份。

## 目录和可复用范围

```text
backend/app/persistence/
  database.py                 独立连接、事务、迁移校验、开发身份初始化、readiness
  models.py                   关系记录到公共 Schema 的转换
  adapter.py                  AB-1 全部 26 方法，独立于 Mock 内部实现
  migrations/001_initial.sql  版本化关系结构、索引、约束、历史不可变触发器
backend/tests/data_contracts/sqlite_provider.py  隔离库共享测试及提交前 SQL 故障探针
backend/tests/test_persistence.py              SQLite 约束、回滚、租约、配置测试
backend/tests/test_persistent_restart.py       两个真实 HTTP 子进程的重启验证
scripts/manage_database.py                    显式、增量数据库管理命令
```

业务服务仍只依赖 DataPort；工厂负责生命周期和实现选择，健康路由检查持久化 readiness。AB-1 方法签名、公开 HTTP Schema 和 TypeScript 生成类型未改。`pyproject.toml` 将 SQL 迁移纳入应用包。

26 个已实现方法如下，输入/返回/缺失/权限等规则仍以 AB-1 为准：

| 分组 | 方法 |
|---|---|
| 用户、项目、成员（8） | user_by_name、user_by_id、role、projects、create_project、members、add_member、remove_member |
| 论文、来源、证据（6） | papers、paper、sources、evidence、remove_paper、import_paper |
| 任务（6） | create_task、task、retry_task、claim_job、complete_job、fail_job |
| 报告（3） | save_report、versions、reports |
| 统计、审计（3） | controlled_statistics、append_audit、audit |

## 配置、迁移及启动

在仓库根目录 PowerShell 执行，使用已安装的开发环境；程序不会自动读取 `.env`。

```powershell
$env:PAPERPILOT_ENV = 'development'
$env:PAPERPILOT_A_MODE = 'persistent'
$env:PAPERPILOT_C_MODE = 'mock'
$env:PAPERPILOT_D_MODE = 'mock'
$env:PAPERPILOT_DATABASE_PATH = '.runtime/paperpilot-dev.sqlite3'
$env:PAPERPILOT_STORAGE_DIR = '.runtime/uploads'
$env:PAPERPILOT_LEASE_SECONDS = '120'

# 可提前显式初始化；启动时也会执行同样的增量迁移与身份初始化。
.\.venv\Scripts\python.exe scripts/manage_database.py migrate --database .runtime/paperpilot-dev.sqlite3
.\.venv\Scripts\python.exe scripts/manage_database.py seed-demo --database .runtime/paperpilot-dev.sqlite3
.\.venv\Scripts\python.exe scripts/manage_database.py check --database .runtime/paperpilot-dev.sqlite3
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

另一个终端执行 `npm.cmd --prefix frontend run dev`。如已有开发进程，先保留其数据并由操作者决定何时停止；本阶段验收没有重启原 8000/5173 进程。

首次仅初始化 alice/bob/eve 三个开发身份，不创建演示项目或论文。登录仍是开发身份切换，不含密码校验。创建项目后可走上传、研究、保存版本和审计流程。健康接口报告 A 模式与限制，界面提示 SQLite 数据可在重启后保留。

迁移使用 `schema_migrations(version, checksum, applied_at)`，同版本可重复执行但不会覆盖；未知已有库、未知版本、已执行迁移被修改或连接失败时明确拒绝启动，无 Mock 回退。迁移命令没有删除/重置功能。后续修改结构应新增版本，不改已执行的 001。当前 readiness 支持版本 1，新增迁移时需同步其期望版本。

切回 Mock：设置 `$env:PAPERPILOT_A_MODE='mock'` 后启动新服务；Mock 不读写 SQLite，也不会删除其数据。开发会话始终在内存，切换或重启后需重新登录。

## 实际存储边界

| 数据 | 当前存储 |
|---|---|
| 用户、项目、成员角色 | 关系表；项目负责人必须对应成员，成员引用有效用户 |
| 论文和项目关联 | 论文元数据、项目关联、SHA-256、受控 storage_key、状态、移出标记 |
| PDF 文件 | B 的文件目录；数据库只存引用，目录需和数据库共同保存 |
| 任务 | 载荷、可信 Context、状态、attempts、租约、错误、结果及结果引用 |
| 来源、证据 | 片段、页码、出处、结构化结果及关系；当前内容由 Mock C 生成 |
| 报告 | 报告、不可覆盖的版本正文、独立引用快照、保存幂等记录 |
| 审计 | 追加式业务审计，按项目/动作/决策/操作者/时间分页筛选 |
| 领域事件 | 与实际变更同事务的最小日志，保留 request/actor/project/operation/resource；无正文、SQL、凭据 |
| 正文、向量、主题 | 未接入完整正文与向量；主题表预留但无写入端口，按主题统计返回实际空结果，不伪造主题 |

数据实体返回 `mock=false`；AI 结果保留 `mock=true`/`synthetic=true`；审计事件保留输入的来源标记。开发身份与 C/D 仍为 Mock，因此健康状态和页面总体仍明确标示开发限制。

## 事务、约束与幂等

每次方法调用独立连接。写事务使用 `BEGIN IMMEDIATE`，读取使用一致的读事务，连接启用外键、5 秒 busy timeout；数据库使用 WAL。本实现依赖 SQLite 的单写者序列化，不是生产并发性能方案。

- 导入：论文 + 项目关联 + 处理任务 + 关联 + 幂等记录 + 领域事件同一事务。数据库 `UNIQUE(project_id,digest)` 防止并发重复；移出论文后摘要仍占用，遵循 AB-1。
- 项目：项目和负责人成员关系同事务；外键延迟到提交检查，配合单负责人唯一索引和角色触发器。
- 任务：合法状态/结果/租约组合由 CHECK 约束。领取和完成/失败使用状态与 attempts 条件更新，拒绝旧尝试覆盖新结果。片段/证据/结果引用与成功状态同一事务。重复相同完成结果不重复插入；旧 worker 的完成和失败均被拒绝。
- 报告：读取最新版本和检查 base_version_id 后，同一写事务插入新正文、递增版本、引用快照和幂等结果；唯一 `(report_id,number)` 加数据库触发器阻止覆盖或删除历史。
- 审计：event_id 幂等；不同内容复用编号报冲突。业务实际变更与最小领域事件同事务。B/D 外部 `append_audit` 是独立事务，失败不能撤销此前已提交业务；领域日志没有投递/消费组件，不等于已完成可靠 outbox。A/D 需确定投递、重试和去重规则。
- 外部 AI 调用在领取提交之后、完成事务之前，不占用长事务；文件写入和数据库提交也非一个事务。未知提交时沿用 AB-1 保留文件并用原幂等键重试，孤立文件清理/断连对账待完善。

约束冲突映射 409；已知未提交的存储故障映射 503 `DATA_UNAVAILABLE`；无法确认提交结果时映射 `DATA_COMMIT_UNKNOWN`。用户可识别的重复导入、版本冲突等保留 AB-1 既有错误码。错误与日志不输出数据库路径、连接凭据或原 SQL。

## 权限及安全边界

B 从开发会话构建可信 Context，逐请求检查成员；SQLiteData 在资源读写、来源校验和任务完成时再检查项目范围与权限，成员管理、审计和论文移出要求负责人。worker 的 Job 只来自可信服务端，校验持久化载荷、Context 和 attempts；不可从 HTTP 构造。

统计仅提供三种固定、带项目条件的参数化查询，HTTP 入口只能调用 SecurityPort。没有任意 SQL 的 DataPort 或 HTTP 接口。当前 SecurityPort 仍是 Mock，不具备 SQL AST、真实模型 SQL、受限身份或数据库层隔离；也没有执行真实攻防实验。SQLite 文件访问者可更改数据库，历史触发器不等于防管理员篡改。

## 测试与重启验证

测试只使用 pytest 的 tmp_path 或系统临时目录；不从用户环境推断数据库位置。Playwright 为两种模式都创建独立文件目录和数据库路径，禁止复用已有服务。

```powershell
# 默认共享 Mock：35 通过、5 个真实库门槛跳过。
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts -q -rs
# SQLite：同一套 40 项全部实际运行，含真实 SQL 失败回滚和 5 个真实库门槛。
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs
# 数据库约束和两个实际服务进程的重启恢复。
.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py -q

$env:PAPERPILOT_E2E_API_PORT = '8100'
$env:PAPERPILOT_E2E_WEB_PORT = '5273'
$env:PAPERPILOT_E2E_DATA_MODE = 'persistent' # 切回 mock 验证另一模式
npm.cmd --prefix frontend run test:e2e
```

自动重启测试在临时库启动真实 Uvicorn 子进程，创建项目、成员、上传、完成合成处理/证据、保存两版报告并读取审计；终止自身子进程，再启动另一进程使用同一临时库。验证旧会话被拒绝、重新登录后编号/正文/引用/证据/任务/审计均保留。测试不打印 Cookie，也不操作已有服务。

人工验证：创建内容并记下项目/报告编号；正常停止自己启动的后端，使用同一数据库和文件目录重新启动；重新登录后从项目进入，核对论文、报告两版与引用。仅刷新浏览器不足以证明重启持久化。

| 并发/恢复能力 | 本阶段证据与限制 |
|---|---|
| 同摘要并发导入 | 两线程、独立 SQL 连接竞争，实际验证仅一个成功、另一个冲突；A 仍需在正式数据库验证 |
| 并发保存版本 | 两线程相同 base_version_id，实际验证一成功一 VERSION_CONFLICT、旧版本保留；A 仍需正式库验证 |
| 并发任务领取 | 两线程实际验证只领取一次；未验证多进程/多副本压力和公平性 |
| 租约恢复 | 实际验证强制过期和自然 1 秒过期、独立适配器恢复、attempts 增加和旧 worker 拒绝；无心跳、最大重试或死信队列 |
| 服务重启读取 | 两个真实服务进程先后运行并验证已提交数据恢复；未做断电、磁盘损坏或 COMMIT 断连故障实验 |
| 正式数据库安全 | 需 A/D 完成角色、RLS、受控视图、真实网关及隔离测试；本阶段未验证 |

AI 因租约到期可能重复调用，fencing 仅阻止旧尝试提交，不保证外部服务 exactly-once。同步 SQLite I/O、全局写锁和五秒超时仅适合小规模开发。没有生产备份恢复、加密、容量/索引性能实验、真实密码或持久化会话；SQLite WAL 备份必须使用一致性备份方式，不能只随意复制活动主文件。

## A 接手清单

可复用 AB-1/错误语义、模型到 Schema 映射思路、事务边界、条件提交、幂等逻辑、共享 provider 和业务重启测试。SQLite SQL、全局写锁、开发身份初始化、文件路径与迁移/readiness 实现均需按正式方案审查，不能直接改名为生产实现。

1. 确认 PostgreSQL/pgvector 选型、正式实体/索引与迁移工具，不增加没有必要的两套长期实现。
2. 保持 26 方法兼容，完善数据库驱动错误分类、COMMIT 未知补偿及文件对账。
3. 在正式库验证跨连接/多进程竞争、CAS、领取/租约/续租、死信、幂等和约束失败全回滚。
4. 与 C 补齐真实文件读取、正文/向量/主题数据结构和结果持久化；与 D 接入受限身份/RLS、不可绕过的网关和审计保护。
5. 增加正式用户认证/会话方案、迁移升级与备份恢复、性能及部署验收。

实际运行结果和未运行项见 [验证记录](verification.md)；简短交接见 [成员 A 摘要](member-a-handoff.md)。
