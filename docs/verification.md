# Phase 1 验证记录

> 当前状态见末节“Phase 2C 任务恢复验收”。此前 SQLite 内容保留为历史实验，不代表团队正式数据库选型，也不表示现有业务工厂仍自动注册它。

验证日期：2026-10-26。平台：Windows，Python 3.12.6、Node.js 24.14.1、npm 11.11.0。完整依赖组合见 `requirements.lock` 与 `frontend/package-lock.json`。

本阶段全部研究数据、用户身份切换、模型与统计结果使用 Mock。下面的通过结果不代表真实数据库、真实 AI 或 SQL 安全实验通过。

## 实际执行及结果

| 命令（仓库根目录，除注明外） | 实际结果 |
|---|---|
| `python -m venv .venv` | 成功建立隔离环境 |
| `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` | 初次因多目录包发现失败；明确 backend 包路径后重新执行成功 |
| `npm.cmd --prefix frontend install --no-audit --no-fund` | 成功，生成 npm 锁文件 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py` | 成功导出 OpenAPI 与 26 个操作的正常/错误 JSON 示例 |
| `npm.cmd --prefix frontend run contracts` | 成功生成前端类型 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与当前应用一致 |
| `.\.venv\Scripts\python.exe -m pytest -q` | 51 项通过；1 条第三方弃用警告 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 通过，24 个文件格式一致 |
| `npm.cmd --prefix frontend run build` | TypeScript 检查及 Vite 构建通过；存在全量组件导入导致的大包体积提示 |
| 前端目录执行 `npx.cmd playwright install chromium` | 安装 Chromium 测试浏览器成功 |
| `npm.cmd --prefix frontend run test:e2e` | 2 项浏览器流程通过，自动启动真实后端和 Vite 服务 |
| `.\.venv\Scripts\python.exe -m pip check` | 无依赖冲突 |
| `docker compose config --quiet` | 配置校验通过 |
| `docker info --format '{{.ServerVersion}}'` | 失败：本机 Docker Linux 引擎未运行 |

最终使用 README 的 Uvicorn 与 Vite 命令分别启动了本机开发服务：后端健康检查返回 ok、前端返回 HTTP 200，前端 `/api/v1/health` 代理也返回 ok。启动日志明确输出 a=mock、c=mock、d=mock、environment=development。默认仅绑定 127.0.0.1。

测试初次发现文本导出路由声明的错误媒体类型与 JSON 错误不一致，已修复并通过全部示例验证。前端初次类型检查发现调用缺少契约要求的请求头参数，已修复；GET 与写请求采用各自参数结构。

第三方警告为当前 Starlette TestClient 对 HTTPX 接口的弃用提示；不影响本次执行结果。Node 测试进程也有终端颜色环境变量提示。未通过隐藏警告来宣称无警告运行。

## 已通过的后端与契约测试范围

- 健康检查、身份及退出流程，开发会话 Cookie 标记。
- UUID 请求关联、统一脱敏错误，验证错误不回显原输入，未知内部异常不泄露详情。
- 请求头和 Origin 检查。
- 服务端项目成员及负责人检查；替换项目、论文、任务、报告编号不会获得其他项目内容。
- 成员移除后，已登录会话后续请求立即失去项目权限。
- 上传入口、内存任务状态转换、重复导入、幂等回放、同键不同参数冲突、失败任务重试。
- 三种检索模式的契约、年份筛选、空结果。
- 正常问答与证据不足；来源伪造或引用不属于本次检索集合时拒绝。
- 五类证据输出及草稿任务。
- 报告版本保存、幂等、基础版本冲突、历史正文不被覆盖、文本导出。
- 移出论文后普通读取/检索/任务不可访问，项目内历史报告快照仍可读取。
- 网关 executed/rejected/failed 的区分；allowed 不等于 executed。
- 网关不可用不调用受控统计备用路径；关键审计写入失败时不调用网关。
- 审计筛选、分页与带时区时间约束。
- Mock/real/production 配置校验，真实适配器未实现时拒绝启动。
- 26 个 HTTP 操作均有正常与异常示例，响应符合导出的 JSON Schema；OpenAPI 与应用一致。

## 已通过的浏览器流程

1. 开发登录 → 文献页上传合成测试 PDF → 任务完成 → 三模式检索入口 → 证据卡片 → Mock 问答 → 点击引用并展开片段 → 生成草稿 → 保存两个版本 → 刷新后读取第二版本 → Mock 统计执行 → 拒绝 → 网关不可用错误。
2. 390×844 视口下登录 → 创建项目 → 文献空状态 → 检索无结果 → 问答证据不足 → 页面无水平溢出。

已人工查看四个桌面页面与移动页面截图。截图和测试产物位于忽略的 `frontend/test-results`，不作为真实论文或安全实验材料。

## 未运行与能力限制

- Docker 镜像构建与容器启动：Docker 引擎不可用，未运行；不能认定 Compose 部署已通过。
- 另一台机器或干净环境安装：未完成；已提供锁文件和启动命令。
- PostgreSQL 持久化、迁移、事务、索引、RLS 和数据库权限：未实现、未测试。
- 真实密码摘要验证与持久化会话：未实现。开发登录可以选择任一演示身份，仅允许本机演示。
- 真实 PDF 正文/页码解析、扫描件和加密识别、嵌入、向量检索及 LLM：未实现、未评测。
- SQL AST、允许清单、受限执行身份、SQL 超时和资源限制、40+20 攻防测试：未实现、未执行。
- 可靠任务队列、崩溃恢复、多进程并发和服务重启后数据保留：未实现；内存状态重启即丢失。
- 上传文件只做大小及 PDF 标记检查，不提供完整文件安全分析。

下一阶段优先接入 A 的用户/项目、论文导入、任务及报告持久化，联合 D 验证项目边界，再与 C 打通真实解析和检索。完成真实网关与数据库权限验证前，不启用真实自然语言统计执行。

## Phase 2A A/B 数据契约审查与冻结

验证日期：2026-10-26。未新增数据库或其他依赖，没有迁移、建库或数据库重置。工作区原有文件和未提交成果保留。

冻结文档：`docs/ab-data-contract.md`（AB-1）；成员 A 摘要：`docs/member-a-handoff.md`。完整 26 方法输入/返回/异常示例：`contracts/data-port-examples.json`。方法名称、参数顺序/必填/default 和公开 HTTP Schema 保持兼容，仅收窄已有合法值的内部类型注解。

本次修正：返回值不再与 Mock 存储共用可变对象；统一规范化任务载荷和幂等指纹；数据边界重新校验项目、来源、编号和版本；按 attempts 拒绝迟到完成/失败；报告和任务成功审计改为提交后；未知提交保留可能已采纳的上传文件；Mock 在提交探针故障时回滚内存变更。

| 实际命令 | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts -q -rs` | 最终 35 项共享行为测试通过，5 项真实数据库门槛跳过 |
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 最终 90 项通过、5 项跳过；保留 1 条既有第三方弃用警告 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 最终通过；开发中发现并修复测试 lambda 赋值和生成脚本导入标注问题 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 32 个文件格式检查通过 |
| `.\.venv\Scripts\python.exe scripts/export_data_examples.py` | 导出 26 方法实际 Mock 示例，含缺失或异常行为；测试检查调用参数可绑定到当前签名 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与应用一致；文件摘要对比也确认原 OpenAPI 和前端 schema.d.ts 内容未变 |
| `npm.cmd --prefix frontend run typecheck` | 通过 |
| `npm.cmd --prefix frontend run build` | 通过；保留既有 Element Plus 全量导入体积提示 |
| 前端目录设置 `PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273` 后执行 `npm.cmd run test:e2e` | 2 项浏览器 Mock 全流程通过，12.4 秒 |

浏览器验证使用独立端口，未关闭或重启原 8000/5173 开发进程。Playwright 配置仅增加可选测试端口与代理参数；未修改前端页面或架构。已有后端进程需要重启才加载新的 Python 代码，Mock 重启会清空内存状态。

### 共享测试适用范围

默认测试仅通过公开 DataPort 方法观察状态，不检查 Mock 字典、SQL 表名或 ORM 内部结构。provider fixture 负责用户初始化、故障注入和环境隔离。Mock 只在指定写入已发生、提交前触发探针，再验证回滚后没有悬空论文/任务、片段、版本或审计事件；此结果不证明 PostgreSQL 事务或约束有效。

5 项跳过项逐项为：重连保留论文/任务/报告/快照、并发同摘要导入唯一、并发报告 CAS、并发 worker 只领取一次、租约过期恢复后拒绝旧 worker。A 提供真实隔离库 provider 后应实际运行，不得把跳过算通过。

真实数据库角色/RLS、事务内领域事件或 outbox、完整正文/向量提交、迁移/readiness、生产认证和真实提交断连补偿仍需 A/C/D/B 联调，本次没有声称完成这些能力。测试中的未知提交场景也是 Mock 模拟，不是真实数据库故障实验。

### 改动清单

新增：`backend/app/adapters/data_rules.py`；`backend/tests/data_contracts/` 的 6 个文件；`scripts/export_data_examples.py`；`contracts/data-port-examples.json`；`docs/ab-data-contract.md`；`docs/member-a-handoff.md`。

修改：`backend/app/adapters/ports.py`、`mock_data.py`、`backend/app/services.py`、`backend/app/routes/literature.py`、`research.py`；`backend/tests/conftest.py`、`test_api.py`、`test_contracts.py`；`pyproject.toml`（仅注册测试标记）；`frontend/playwright.config.ts`；`README.md`、`docs/api-contracts.md`、`docs/development-guide.md`、本文。

未删除文件。依赖清单及锁文件没有增加数据库包，未提交/推送 Git。

## Phase 2B 基础持久化验收

验证日期：2026-10-26；同一 Windows/Python/Node 环境。选择标准库 SQLite 临时开发实现；仓库只有 PostgreSQL/pgvector 预期，没有已落地正式选型。AB-1 26 方法、公开 Schema、OpenAPI、前端生成类型及两个依赖锁文件未改。本节为当前状态，前述 Phase 1/2A 限制保留为历史记录。

### 实际执行及结果

| 命令（仓库根目录，除注明外） | 实际结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 最终 **121 passed, 5 skipped, 0 failed**，15.30 秒；1 条既有第三方弃用警告 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | **40 passed, 0 skipped, 0 failed**，5.87 秒；不是 Mock 持久化模拟 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py -q` | 最终 **30 passed**，8.61 秒，含实际 CLI 迁移/初始化/检查子进程 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistent_restart.py -q` | **1 passed**，2.85 秒；最终全量测试再次执行通过 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 40 个文件通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与应用一致 |
| `npm.cmd --prefix frontend run typecheck` | 通过 |
| `npm.cmd --prefix frontend run build` | 类型检查及构建通过；保留既有大包提示（JS 约 1.06 MB） |
| 设置 `PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=persistent` 后 `npm.cmd --prefix frontend run test:e2e` | **2 passed**，13.1 秒；A 为 SQLite，C/D 仍为 Mock |
| 相同测试端口，设置 `PAPERPILOT_E2E_DATA_MODE=mock` 后执行上述浏览器命令 | **2 passed**，12.9 秒 |
| `.\.venv\Scripts\python.exe -m pip check` | 无应用依赖冲突 |
| Python 子进程执行 `python -m pip --isolated --disable-pip-version-check wheel . --no-deps --index-url https://pypi.org/simple --wheel-dir <系统临时目录>`，读取生成 wheel 的 ZIP 目录 | 构建成功，确认包含 `app/persistence/migrations/001_initial.sql`；使用已声明的隔离构建工具，未向应用虚拟环境新增运行依赖 |

默认全量测试的 5 个跳过仅来自默认 Mock provider：重连、并发重复导入、并发报告保存、并发领取、租约恢复。这五项全部通过显式 SQLite provider 另行实际执行，不能把默认跳过直接计作通过。两次命令测试范围有重叠，不将 121 和 40 相加声称独立用例数。

新增重启测试初次因测试客户端手工旧 Cookie 与重新登录 Cookie 的路径不同而失败；清除模拟旧会话后重新登录，验证通过。首次额外 wheel 检查使用 `--no-build-isolation` 失败（当前虚拟环境没有 setuptools）；改用项目原已声明的隔离构建环境后成功，没有修改依赖来隐藏问题。

### 已实际验证的真实存储能力

- 共享 AB-1 行为测试在隔离 SQLite 文件上运行，用户/项目/成员、论文、任务、来源、证据、报告快照、审计、缺失资源及跨项目拒绝均通过。
- 独立 SQL 连接启用外键；直接 SQL 验证用户非空、项目/成员完整性、负责人保留、项目摘要唯一、状态合法、报告版本唯一和历史不可覆盖。
- 在写入后/提交前触发实际 SQL NOT NULL 失败，验证论文、关联、任务、幂等记录、片段、报告版本/引用、审计等相应事务回滚。单独验证领域日志随导入同时提交或回滚。
- 两线程分别发起真实 SQLite 事务，验证同摘要只有一个成功、相同报告基版本只有一个成功、同一任务只领取一次。
- 强制租约过期和自然 1 秒租约过期均验证恢复后 attempts 递增；独立适配器恢复后拒绝旧 worker 的完成/失败，最终只保存一套片段。
- 两个真实 Uvicorn HTTP 子进程依次使用同一临时 SQLite 文件。第一进程创建项目、Bob 成员、论文和完成任务、证据及两版报告；终止后第二进程拒绝旧会话，重新登录后读回相同项目、成员、论文/来源、完成任务、证据、两版正文/引用和原审计事件。
- SQLite 前端流程实际包含创建项目、上传、合成处理、检索/问答/来源、证据、两次报告保存与刷新读取、统计演示/拒绝/网关故障。移动空状态流程也通过。
- CLI `migrate` 两次、`seed-demo` 两次、`check` 一次在专用临时库成功，已有内容未被重复初始化覆盖。未知库、非法文件、迁移校验和变化或未知版本拒绝启动，未回退 Mock。

全部持久化测试使用 pytest tmp_path；浏览器测试使用独立系统临时目录。没有读取或迁移用户默认 `.runtime/paperpilot-dev.sqlite3`，结束时确认该文件不存在。未停止原 8000/5173 服务（PID 67872/48560）；8100/5273 测试服务结束后已退出。没有读取真实密钥、提交或推送 Git。

### 并发、安全和交付限制

本机两线程结果只证明当前 SQLite 实现的有限竞争行为。多进程/多副本高并发、长期锁争用、公平性、心跳续租、重试上限/死信队列、外部 AI exactly-once 均未验证或未实现。服务重启读取测试不等于断电、磁盘损坏或 COMMIT 确认丢失实验。

业务变更同事务记录最小 domain_events；没有可靠投递/消费组件。外部审计、文件存储与外部 AI 不与业务共用大事务，正式补偿/对账仍待 A/B/D 完善。历史触发器不抵御数据库文件所有者或管理员篡改。

真实密码、持久化会话、完整正文/向量、主题标注、真实 PDF/AI、SQL AST/受限数据库身份/RLS/安全网关与 40+20 攻防测试未实现或未执行。Docker 容器构建和干净机器部署本阶段未执行，既有 Compose 仍为 Mock 开发配置。备份恢复、正式迁移升级及性能实验待 A 接手。

本阶段达到**本机前端和业务 API 持久化联调可用**，不是生产部署或正式数据库安全验收。下一步由 A 确认正式库并复用共享测试；与 C 联调真实文件处理前，先固定文件读取和正文/片段结果的存储边界。

### 本阶段改动清单

新增 10 个文件：`backend/app/persistence/` 的 `__init__.py`、`adapter.py`、`database.py`、`models.py`、`migrations/001_initial.sql`；`backend/tests/data_contracts/sqlite_provider.py`；`backend/tests/test_persistence.py`；`backend/tests/test_persistent_restart.py`；`scripts/manage_database.py`；`docs/persistence.md`。

修改：`.env.example`、`.gitignore`、`pyproject.toml`（SQL 打包）；`backend/app/config.py`、`adapters/factory.py`、`main.py`、`routes/identity.py`；`backend/tests/data_contracts/conftest.py`；`frontend/src/state.ts`、`App.vue`、`pages/ResearchPage.vue`（持久化提示）；`frontend/playwright.config.ts`、`frontend/tests/workflow.spec.ts`（双模式隔离测试）；`README.md`、`docs/architecture.md`、`docs/development-guide.md`、`docs/ab-data-contract.md`、`docs/member-a-handoff.md`、本文。

未删除文件，未覆盖其他未提交文件；端口及业务服务、前端架构保持不变。SQLite 是独立 SQL 适配器，不是 Mock 字典序列化。

## Phase 2B 中立调整验收（当前）

本次按用户确认方案解除 persistent 与 SQLite 的默认绑定。数据库引擎、正式模型/迁移和驱动由 A/团队决定；没有新增或修改 `backend/app/persistence/` 下任何文件，没有新增数据库依赖或执行用户数据库迁移。

### 已完成与兼容性

- 新增 DataBinding(data, ready, close)，服务端显式注册 A 实现；persistent/real 未注册拒绝启动。独立 readiness 在启动和健康接口检查，失败脱敏；服务退出调用可选关闭函数。B 工厂不访问数据库属性或默认迁移/建库。
- 上传仅在 AB-1 明确未提交的错误时清理本次文件；未知/未分类提交保留文件并返回 DATA_COMMIT_UNKNOWN。文件创建冲突不删除原文件；部分写入故障不调用 import_paper。
- complete_job 的提交结果未知或不明异常不调用 fail_job；明确成功后审计失败继续保留成功状态。
- 浏览器上传和报告相同载荷重试保留键，成功或改变载荷才进入新操作。未新增跨刷新幂等恢复队列，未提供自动孤立文件删除。
- 共享测试新增重连后成员/证据/审计和幂等记录恢复；报告历史、引用快照、项目授权及迟到 worker 规则继续使用原 AB-1。
- 摘要比对确认 ports.py、data_rules.py、mock_data.py、schemas.py、OpenAPI、前端生成类型、两个依赖锁文件、全部已有持久化模型/迁移/适配器均未改；契约样例签名检查及 OpenAPI 检查通过。

### 本次实际命令与结果

| 命令（根目录） | 最终结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | **107 通过、37 跳过、0 失败**，6.77 秒 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | **41 通过、0 跳过、0 失败**，6.57 秒；显式历史实验 provider，不是正式选型验收 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs` | **31 通过、0 跳过、0 失败**，10.22 秒；含实际两个 HTTP 子进程重启读取 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 43 文件通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与默认应用一致 |
| `npm.cmd --prefix frontend run typecheck` | 最终通过；初次发现随机 UUID 默认参数推断过窄，增加 string 注解后修复 |
| `npm.cmd --prefix frontend run build` | 类型检查、构建通过；保留既有大包提示，JS 约 1.06 MB |
| 设置 `PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=mock` 后 `npm.cmd --prefix frontend run test:e2e` | **4 通过、0 失败**，14.1 秒 |
| `.\.venv\Scripts\python.exe -m pip check` | 无依赖冲突 |

37 项默认跳过由 **31 项历史 SQLite 实验**和 **6 项真实数据库门槛**组成。前者通过 --legacy-persistence 显式另跑；后者为重连、重复导入竞争、版本竞争、领取竞争、租约恢复、新增成员/证据/审计/幂等重连检查。SQLite provider 已实际运行它们；A 的正式数据库 provider 尚未提供，因此正式库恢复、并发和安全仍未验收。各命令存在重叠，不把通过数相加当作独立用例数。

新增注册测试使用内存委托替身，只证明 B 的组合边界，不证明真实持久化。两条新增浏览器测试先向 Mock 后端实际提交，再模拟确认响应丢失，验证上传/报告重试同键且报告仍只有第一版；这不是数据库 COMMIT 断连实验。既有完整研究流程与移动空状态也通过。

测试只使用 tmp_path/系统临时目录，未创建或触碰默认 `.runtime/paperpilot-dev.sqlite3`。原 8000/5173 服务 PID 67872/48560 未变，测试 8100/5273 服务已退出。未读真实凭据、未删除原文件、未重置/提交/推送 Git。

### 本轮文件清单

新增：`backend/app/adapters/data_binding.py`、`backend/tests/test_data_binding.py`、`backend/tests/legacy_sqlite_app.py`、`docs/persistence-integration.md`。

修改：`.env.example`、`pyproject.toml`（仅测试标记）；`backend/app/adapters/factory.py`、`config.py`、`main.py`、`routes/identity.py`、`routes/literature.py`、`services.py`；`backend/tests/conftest.py`、`data_contracts/test_real_database.py`、`test_api.py`、`test_persistence.py`、`test_persistent_restart.py`；`frontend/playwright.config.ts`、`frontend/src/App.vue`、`api/client.ts`、`pages/PapersPage.vue`、`pages/ResearchPage.vue`、`frontend/tests/workflow.spec.ts`；`README.md`、`docs/ab-data-contract.md`、`api-contracts.md`、`architecture.md`、`development-guide.md`、`member-a-handoff.md`、`persistence.md`、本文。

A 待决定：数据库及驱动/迁移、正式约束索引与事务隔离、lease/续租/恢复、幂等保留及未知提交对账、文件/正文/向量结构、审计/outbox 与数据库权限、正式认证、备份性能。B 下一步可独立推进任务重连交互；当前只能声称 B 接入准备和 Mock 业务可用，不能声称正式持久化已接入或生产安全验收完成。

## Phase 2C 任务恢复验收（历史阶段记录）

验证日期：2026-10-26。按用户确认方案实施，无新增依赖、数据库、模型或迁移。检查确认原 Task/API/服务端授权和 AB-1 幂等机制已存在；本次补前端刷新/断网/超时恢复与 B 边界测试。完整行为与风险说明见 [任务恢复](task-recovery.md)。

### 实际改动

新增 5 个文件：`frontend/src/composables/useTaskRecovery.ts`、`frontend/src/components/TaskRecoveryPanel.vue`、`frontend/tests/task-recovery.spec.ts`、`backend/tests/test_task_recovery.py`、`docs/task-recovery.md`。

修改 12 个文件：`frontend/src/api/client.ts`、`frontend/src/composables/useAction.ts`、`frontend/src/pages/PapersPage.vue`、`frontend/src/pages/ResearchPage.vue`、`frontend/tests/workflow.spec.ts`；`backend/app/routes/literature.py`、`backend/app/services.py`；`README.md`、`docs/api-contracts.md`、`docs/development-guide.md`、`docs/persistence-integration.md`、本文。

未删除或重置任何文件。源文件摘要比对确认以下内容不变：AB-1 端口、DataBinding、工厂、MockData、data_rules、公共 schemas、OpenAPI、生成的前端类型、两个依赖锁文件，以及 `backend/app/persistence/` 全部已有模型/迁移/适配器。原有工作区未提交成果保留，未提交或推送 Git。

### 已完成流程

- 共用恢复模块先同步锁定操作，先保存最小请求记录再 POST；确认后保存 task_id。刷新只 GET 原任务，四种任务均能重新展示已有结果。
- 记录只保留在当前标签页 sessionStorage，并按用户/项目/页面隔离；不保存密码、认证令牌、研究问题、标题、文件、生成正文或来源片段。操作失败/卸载后不跨用户应用迟到结果。
- pending/processing、succeeded、failed 明确展示；60 秒轮询截止或网络错误只标记当前状态未知，阻止新生成并保留原任务重新查询入口。
- 已知 task_id 的重新查询和成功结果选择不 POST。重复事件派发也由同步代码锁拦截，不能只依赖按钮禁用。
- 没有确认编号的未知请求不自动再次提交；用户恢复相同输入并明确点击核对，才复用原键。上传需原文件/标题；问答/草稿需原问题和原文献编号快照；证据使用原 paper_id。
- failed 的明确重试先查状态，复用原 task_id；确认丢失时通过 GET 核对，不自动再次 retry。
- 每次任务 GET 仍调用会话/项目授权并由 DataPort 校验范围；未分类查询故障安全返回 TASK_QUERY_UNAVAILABLE。研究任务创建的未分类提交故障返回 DATA_COMMIT_UNKNOWN，不回退 Mock。

### 实际命令与结果

| 命令（根目录） | 最终实际结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py -q` | 新增 **11 项通过**，0.57 秒；使用 Mock，不证明数据库持久化 |
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | **118 通过、37 跳过、0 失败**，6.68 秒 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | **41 通过、0 跳过、0 失败**，6.84 秒；仅历史实验 provider |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs` | **31 通过、0 跳过、0 失败**，9.07 秒；保留历史实验，不代表正式库验收 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 44 个文件通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与默认应用一致 |
| `npm.cmd --prefix frontend run typecheck` | 最终通过；接入过程中旧模板 task/currentTask 引用导致初次失败，完成统一面板接入后修复 |
| `npm.cmd --prefix frontend run build` | 类型检查及构建通过，5.16 秒；保留既有大包提示，JS 约 1.07 MB |
| 设置 `PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=mock` 后 `npm.cmd --prefix frontend run test:e2e` | **21 通过、0 失败、0 跳过**，49.8 秒；17 项新增浏览器测试及 4 项保留流程 |
| `.\.venv\Scripts\python.exe -m pip check` | 无依赖冲突 |

默认 37 项跳过仍为 31 项历史 SQLite 实验和 6 项真实数据库门槛，未计作通过。上述显式实验测试另行实际运行；正式数据库 provider 尚未提供。各命令有覆盖重叠，不将通过数相加声称独立用例总数。

### 本次测试证明的范围

后端新增 11 项验证：pending 和完成任务的重复 GET 不 claim/不重试/不调用 AI/不改状态；匿名、非成员、跨项目、撤销成员和不存在任务的安全响应；临时查询错误不泄漏 provider 信息；四种创建流程的同键回放；提交已发生但确认异常后的原键恢复。

浏览器新增 17 项验证：四类任务刷新读取原 ID/结果；查询超时后同 ID GET；模拟时钟超过 60 秒不标记失败；服务端失败在刷新后仍展示安全错误且不自动重试；上传/问答/证据同周期连续点击只有一次创建；未知问答/上传刷新后不自动 POST、错误原输入被拒绝、正确输入同键核对；不存在任务不自动替代；查询结果未知时即使改变输入并直接派发点击仍不创建；浏览器恢复存储失败不发送创建；失败重试确认丢失只查询原 ID；选择原已完成任务仅读取旧结果。既有完整研究、移动布局、上传及报告版本幂等流程保留并通过。

客户端防重复由请求计数、同步锁和实际浏览器行为验证；服务端幂等由单独后端同键/同任务行为验证。网络、确认丢失和超时通过浏览器拦截及模拟时钟制造，不是真实数据库 COMMIT 故障、断电或跨进程持久化实验。

### 限制及后续

- sessionStorage 只保留同一标签页最多 20 条/页面的轻量恢复记录，不提供跨设备、关闭标签页后或用户清理存储后的可靠恢复。
- 原始输入不保存在浏览器；未知上传需重新选择原文件，未知问题需重填。没有按幂等键查询的 API，因此明确核对是同键重放，在原请求未提交时可能创建任务；不会后台盲目换键提交。
- 幂等记录保留期及正式恢复待 A 确认。空 paper_ids 在当前 API 表示服务端当前范围；原空范围后来新增论文可能导致同键冲突，保留待核对状态，不擅自改语义或新增端口。
- failed retry 的状态条件/attempts 隔离与创建键幂等不同，不能声称全局 exactly-once。报告版本保存不是异步任务，其跨刷新未知保存对账仍未扩展。
- 正式数据库的持久化恢复、并发/租约、安全权限和真实 AI 仍未验收；历史 SQLite 的通过结果仅属原实验。

本次测试均使用隔离临时文件/目录。原 8000/5173 服务 PID 67872/48560 未变；8100/5273 测试服务已结束；默认运行 SQLite 文件仍不存在。未读取或输出真实凭据，未安装新依赖或执行用户数据库迁移。

建议下一步先与 A 对齐幂等保留期、按键对账和空范围语义；B 可继续做任务历史/恢复管理交互，所有状态仍以授权后的服务端结果为准。

## Phase 3：A/C/D 并行开工准备（当前）

验证日期：2026-10-26。先实际检查端口、调用方、Mock、DataBinding、契约/样例、现有测试及脚本，再按用户明确确认的范围实施。当前阶段不继续扩展任务恢复或业务功能，不决定数据库、不实现正式持久化、AI 算法或 SQL 网关。

### 检查结论与本次交付

AB-1 已有26方法、独立 DataBinding 和可替换共享测试。C 的5方法和 D 的2方法足以开始独立实现，无需修改端口或公开 Schema；实际阻塞是 C/D real 被配置层无条件拒绝、工厂没有注入入口，且缺完整 C/D 交接和独立测试。现已补齐 create_app → create_adapters 的 ai_factory/security_factory，每个工厂接收同一个已注册 DataPort；未注册、缺方法、直接返回 Mock 或构造异常均拒绝启动，不静默回退。Mock 配置传真实工厂也拒绝，避免被忽略。

启动只记录模式/安全状态；构造异常脱敏。非 Mock 开发应用标注 ADAPTER DEVELOPMENT，health 明确 registered_*_unverified；正式认证未完成，production 始终拒绝。注册的内存委托替身只用于装配测试，不能证明真实 AI 或 SQL 安全有效；C/D 资源生命周期在交接中明确需成员与 B 协调，没有扩展端口生命周期方法。

新增 C/D 可复用共享测试及显式 provider：默认 Mock 验证结构行为，成员通过 --ai-contract-provider/--security-contract-provider 提供隔离 fixture。生成器实际调用全部5个 C 方法和2个 D 方法、输出拒绝/失败/不可达/证据不足/审计样例。三份成员交接均含职责、代码位置、完整方法、正常/异常示例、启动测试、交付、验收、非职责和未决事项；JSON 示例逐一比对实际机器样例。统一入口见 [开发指南](development-guide.md)。

### 文件清单（相对本阶段开始时的文件摘要）

新增14文件：

- `backend/tests/ai_contracts/{__init__.py,conftest.py,harness.py,test_ai_port.py}`（4）。
- `backend/tests/security_contracts/{__init__.py,conftest.py,harness.py,test_security_port.py}`（4）。
- `backend/tests/test_module_registration.py`、`backend/tests/test_module_examples.py`。
- `scripts/export_module_examples.py`、`contracts/module-port-examples.json`。
- `docs/member-c-handoff.md`、`docs/member-d-handoff.md`。

修改13文件：`.env.example`、`README.md`；`backend/app/adapters/factory.py`、`backend/app/config.py`、`backend/app/main.py`、`backend/app/routes/identity.py`；`backend/tests/conftest.py`；`docs/api-contracts.md`、`docs/architecture.md`、`docs/development-guide.md`、`docs/member-a-handoff.md`、`docs/persistence-integration.md`、本文。

摘要检查确认没有删除文件；`ports.py`、AB-1 文档、DataBinding、data_rules、三类 Mock、schemas、OpenAPI、既有数据端口样例、全部前端源码/测试/生成类型、pyproject和依赖锁文件、全部历史 SQLite 模型/迁移/适配器均未变。不覆盖或清理先前未提交成果，未执行 Git 重置、提交或推送。

### 实际执行命令与结果

| 命令（仓库根目录） | 本轮实际结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | **150通过、37跳过、0失败**，6.67秒；新增32项=注册10+C契约10+D契约8+样例/文档4 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | **41通过、0跳过、0失败**，5.42秒；显式历史实验，不是正式库验收 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs` | **31通过、0跳过、0失败**，9.64秒；保留历史实验及两个HTTP进程重启用例 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 最终55文件通过；新增文档示例测试初次有1处换行格式问题，修正后通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI与默认应用一致，无重新生成 |
| `.\.venv\Scripts\python.exe scripts/export_module_examples.py` / 同命令加 `--check` | 生成新 C/D 合成示例后，比对通过；不连接模型/数据库 |
| `npm.cmd --prefix frontend run typecheck` | 通过 |
| `npm.cmd --prefix frontend run build` | 通过，4.86秒；保留既有大包提示，JS约1.07MB |
| 设置 `PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=mock` 后 `npm.cmd --prefix frontend run test:e2e` | **21通过、0失败、0跳过**，53.1秒；原流程回归，无新增前端业务 |

37默认跳过=31历史 SQLite opt-in +6真实数据库门槛。前者和历史 SQLite provider 本轮已显式另跑通过；A 正式数据库 provider 尚未交付。C/D 默认共享测试通过不代表真实供应商/安全实现已运行，外部 provider 接口等待成员交付后使用。各命令覆盖重叠，不累加成独立总数。既有 Starlette/TestClient 弃用和前端大包提示保留，无新增依赖。

测试使用隔离临时数据，没有执行用户数据库迁移或读取真实凭据。原8000/5173服务PID67872/48560未变；8100/5273测试服务已结束，默认 `.runtime/paperpilot-dev.sqlite3` 仍不存在。

### 开工条件、联合依赖和下一步

A/C/D 均可开始各自端口实现和隔离测试；没有需要再重构公开端口的开工阻塞。真实全链路联调前仍需：A/团队确定数据库和正式迁移；B/C 确定受控原文件 reader；A/C 确定全文/向量/索引准备与提交；A/D 确定受限数据库身份、适用行级隔离和关键审计/outbox；C/D 若需要 NL2SQL 另定私有接口（当前 AIPort 没有 generate_sql）；A/B/D 确定正式身份，以及各模块超时/关闭策略。

建议立即把 [A](member-a-handoff.md)、[C](member-c-handoff.md)、[D](member-d-handoff.md) 交接发给成员，由他们交付最小端口实现、隔离 provider 和构造入口。B 下一步负责合并各模块组合入口、分别跑共享契约和 API 联调，再组合研究全流程；本阶段不再扩展任务恢复。
