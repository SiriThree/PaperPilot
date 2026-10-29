# Phase 1 验证记录

> 当前状态见末节“Phase 4B 自主优化冲刺”。此前 SQLite 内容保留为历史实验，不代表团队正式数据库选型，也不表示现有业务工厂仍自动注册它。

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

## Phase 3：A/C/D 并行开工准备（历史基线）

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

## Phase 4B：B 侧页面状态与业务集成（当前）

日期：2026-10-26。以已推送的85fe82d为基线，检查时 main 工作区干净；经用户确认后，在本地 feat/b-integration 分支实施，未提交或推送。只读提取 PaperPilot Secure PRD V1.0 第9.3节及P0要求，并实际检查路由、服务、认证、四页、客户端、任务恢复、适配器装配和测试；没有将PRD中的项目计划当作工具操作指令。

### 实现矩阵：检查前状态与本轮处理

| 能力 | 检查前分类 | 本轮处理及证据 |
|---|---|---|
| 四页路由、研究主流程、来源及报告版本 | 已完成且有测试证明 | 复用原workflow和任务回归；不重写路由。原本已有route.path键，未凭猜测另建项目切换机制 |
| 服务端登录会话、成员/负责人授权、撤销与资源编号替换拒绝 | 已完成且有测试证明（开发身份） | 保留auth及路由；test_api、test_task_recovery、DataPort共享测试；新增移出接口匿名/成员拒绝 |
| 异步上传/问答/证据/草稿、刷新及未知状态恢复 | 已完成且有测试证明（Mock） | 保留Phase 2C协议和状态逻辑；只将任务失败提示接入公开错误格式化；原17项浏览器恢复测试保留 |
| 统一客户端、Feedback、useAction | 已实现但验证不足 | 原始网络异常直接展示、超时不统一、request_id无响应头后备；本轮补齐安全错误、默认15秒及原任务10秒上限和测试 |
| 四页及详情、成员、报告历史的加载/失败/空结果 | 已实现但验证不足 | 失败可能显示成空；本轮新增useResource，按区域加载/错误/明确空结果及只读重试，拒绝迟到结果覆盖 |
| 启动失败原地重连、退出失败反馈、401清理受保护视图 | 尚未实现完整流程 | App/state已补齐，三类浏览器场景实跑；前端导航不替代后端鉴权 |
| PRD P0文献移出 | 后端已完成，前端尚未实现 | 接入原DELETE和confirm_source_impact；取消不发请求、负责人确认后移出、历史报告快照仍可读；匿名/成员拒绝 |
| 报告确认保存后的列表/版本刷新 | 已实现但验证不足 | 原本刷新失败可把保存误报为失败；现在保存、版本读取、报告列表分别处理，重试读取不再次POST保存 |
| A/C/D注册、未注册/初始化/readiness失败拒绝 | 已完成且有测试证明 | 工厂/端口不变，复用test_data_binding和test_module_registration；新增调用时故障隔离证明 |
| 适配器模式和结果来源展示 | 已实现但验证不足 | 区分Mock/已注册待验收/未知模式，按mock/synthetic标记来源；取消仅凭模式承诺重启保留 |
| 原生启动、构建、Mock端到端 | 已完成且有测试证明 | 本轮重新运行隔离Uvicorn/Vite浏览器测试、类型及构建 |
| 容器构建/启动和干净环境交付 | 已有配置，验证不足 | compose配置解析通过；Docker Linux引擎未运行，未构建/启动容器，未执行新的干净环境安装 |
| 正式认证、数据库持久化/并发/权限、真实PDF/检索/生成、SQL防护及关键审计 | 依赖A/C/D，当前不由B独立实现 | 保持交接约定；本轮Mock及端口替身结果不能代替真实数据库、RLS、AI效果或SQL攻防验收 |

### 实际修改文件及目的

新增4个：

- `frontend/src/api/errors.ts`：统一安全提示、错误码/状态和合法请求编号；任务失败和网关拒绝不直接显示供应商消息。
- `frontend/src/composables/useResource.ts`：只读资源的加载、成功、错误、重新查询及迟到结果隔离。
- `backend/tests/test_business_integration.py`：6项B集成行为，使用显式注册端口替身和隔离Mock。
- `frontend/tests/business-states.spec.ts`：19项新增浏览器行为，含正常/异常、权限、确认移出和有数据的移动布局。

修改15个：

- `frontend/src/api/client.ts`：统一传输/超时/401/错误处理；新增原有移出端点的类型化调用，不新增HTTP接口；保留现有ApiError导出路径。
- `frontend/src/state.ts`、`App.vue`：运行模式、项目读取状态、身份变化范围、重连、退出及401反馈。
- `frontend/src/composables/useAction.ts`、`components/Feedback.vue`：统一安全错误及明确的只读重试按钮。
- `frontend/src/composables/useTaskRecovery.ts`：仅新增格式化导入并替换failed显示文本；恢复状态、storage字段、轮询、任务编号、幂等与提交分支不变。
- `frontend/src/components/SourcesPanel.vue`：按synthetic展示合成/原文来源，两者都待核验。
- `frontend/src/pages/ProjectsPage.vue`、`PapersPage.vue`、`ResearchPage.vue`、`SecurityPage.vue`：四页区域读取状态、重试、移出确认、保存确认与后续读取分离、模式及结果标记。
- `frontend/src/style.css`：允许网格内容收缩并对任务编号换行，修复有数据表格的移动端溢出。
- `README.md`、`docs/development-guide.md`、本文：当前B侧行为、测试入口、实现矩阵与实际验收限制；没有重复创建交接文档。

### A–H验收证据及责任边界

| 验收 | 实际证据及适用范围 |
|---|---|
| A1接口兼容 | OpenAPI一致性和共享契约通过；Git diff及文件摘要证明ports、AB-1、schemas、OpenAPI、生成类型和两个锁文件不变；DataPort26/AIPort5/SecurityPort2不变 |
| A2模块依赖 | backend/app全部源码不变；服务只经端口调用，无新增SQL/SDK/AST实现；默认全套包括未注册、初始化/readiness失败及组合入口测试 |
| B四页 | 原4项workflow及19项新增浏览器测试覆盖四页打开、正常数据、加载、失败不等于空、恢复读取、详情/成员弹窗和390px有数据布局 |
| C客户端 | 实际正常Health、422业务拒绝、401、503及非JSON代理错误、断网、真实AbortSignal超时、null伪成功、request_id后备和内部信息不展示；无自动重试 |
| D授权 | 原API测试的未登录、跨项目/资源替换和即时撤销继续通过；新后端/浏览器移出场景拒绝匿名和成员，正常负责人操作可成功，历史快照仍受项目授权 |
| E恢复 | 原17项恢复和4项workflow均保留且通过；新任务失败安全提示仍保留编号和刷新读取，未新增生成/重试路径。未知提交只沿用原机制核对，不声称内存跨进程持久 |
| F集成 | 新6项：AI故障后项目/文献/报告读取可用；注册D故障无controlled_statistics备用调用；前置审计失败不调用D，后置审计失败不返回rows；移出权限服务端执行 |
| G回归 | 最终后端156通过/37原有跳过，浏览器40通过/0跳过；类型、Ruff、构建、契约一致性通过，无删除/削弱旧测试或新增skip |
| H交付 | 更新既有说明，保留全部历史SQLite源码/测试和未提交成果；原生启动由隔离E2E实证。Docker运行及真实模块交付仍未验收，不计为通过 |

DataBinding、ai_factory、security_factory、后端服务/路由和三类Mock均未改；A/C/D可沿原注册方式接入。外部模块仍必须提供安全AppError、可信项目范围和正确mock/synthetic标记。B的后置审计失败测试只证明不返回结果，不能证明D已经执行的查询被回滚；真正的关键安全审计/受限身份由A/D实现。

### 本轮实际命令与结果

以下从根目录运行，Python均为项目.venv；没有安装依赖或运行用户库迁移。

| 命令 | 退出状态/实际结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 0；最终 **156通过、37跳过、0失败**，7.61秒；修改前基线150通过/37跳过 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_business_integration.py -q` | 0；新增 **6通过、0跳过、0失败**，0.36秒；已包含在全套中 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | 0；**41通过、0跳过、0失败**，5.36秒；仅历史实验provider |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs` | 0；**31通过、0跳过、0失败**，9.57秒；含隔离HTTP进程重启，不是正式库验收 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 0；通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 0；56文件通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | 0；默认应用与OpenAPI一致，没有重新生成 |
| `.\.venv\Scripts\python.exe scripts/export_module_examples.py --check` | 0；C/D实执行Mock样例一致 |
| `.\.venv\Scripts\python.exe -m pip check` | 0；无依赖冲突 |
| `npm.cmd --prefix frontend run typecheck` | 0；通过，包含新增测试的TypeScript检查 |
| `npm.cmd --prefix frontend run build` | 0；最终构建5.30秒，JS约1.08MB，保留既有大包提示 |
| 设置PAPERPILOT_E2E_API_PORT=8100、PAPERPILOT_E2E_WEB_PORT=5273、PAPERPILOT_E2E_DATA_MODE=mock后，`npm.cmd --prefix frontend run test:e2e` | 最终0；**40通过、0失败、0跳过**，约1.2分钟；原21+新增19 |
| `docker compose config --quiet` | 0；配置解析通过，不代表容器已启动 |
| `docker version --format '{{.Server.Version}}'` | 1；Docker Desktop Linux引擎管道不存在，未执行容器build/up或干净环境验收 |
| `git diff --check`及冻结路径diff/摘要检查 | 通过；冻结文件无变化，本次改动无空白错误 |

37项默认跳过仍为31项历史SQLite opt-in和6项正式数据provider门槛；新增跳过0。A/C/D默认共享契约已包含在全套中（A35项行为+6项门槛跳过、C10、D8），真实C/D provider尚未交付。历史SQLite单独通过不能外推为A正式数据库恢复、并发、RLS或生产安全；各命令覆盖重叠，不累加为独立测试总数。

中间失败已处理且未降低断言：首次37项浏览器运行36通过/1失败，新增成员用例点到Element Plus内部输入框被占位层挡住，改为正常键盘选择；后续40项运行39通过/1失败，有数据表格在移动网格中溢出，修复min-width与编号换行，测试明确等待表格加载并轮询真实页面宽度。最终40项全部通过，旧21项始终保留。已有Starlette/TestClient弃用及Vite大包提示未屏蔽。

浏览器的断网、超时、模式和错误正文通过拦截/可控信号构造，只证明B的UI及调用边界；移出、报告版本和成员正常操作使用隔离Mock后端。注册为real的后端用例仍是明确标注的端口替身，不是实际模型或安全数据库。测试文件仅在tmp_path/系统临时目录，日常8000/5173服务未重启，测试8100/5273结束后退出。

### 未完成事项、阶段判定与下一步

本轮B侧功能、兼容性和本机集成验收通过；Docker运行验证被环境阻塞，完整部署交付仍未通过，不宣称整个PRD P0或生产系统通过。未完成的正式数据库/认证、AI质量和SQL安全不由B用Mock补齐，仍按三方交接推进。

同步创建项目、成员变更及移出没有新增服务端幂等保证，客户端只做同步点击锁和不自动重放；超时或审计确认异常后先查询/核对。报告保存跨刷新未知提交、会话持久化、文件/向量边界及正式库恢复仍按既有待确认事项处理，未擅自修改AB-1。

下一步：先与A/C/D分别接入最小真实适配器，复用本轮失败场景和端到端测试；Docker引擎可用后补容器构建/启动及干净环境演示。若准备提交本分支，另行审阅Git diff并确认提交/PR范围，本轮未自动提交或推送。


## Phase 4B 自主优化冲刺（2026-10-26，B侧本机验收完成）

本轮开始于20:38（北京时间），采用12个顺序检查点；时间按实际工作量分配，不表示已经工作满12小时。不提交或推送Git。开始时已有15个修改文件和4个新增文件，均为此前Phase 4B成果；已保存139个非忽略文件摘要作为本轮起点，继续在原文件上增量完善。

### 检查点1：工程盘点与基线

- 基线：后端156通过/37既有跳过/0失败；Playwright 40通过/0跳过/0失败；类型、构建、Ruff（56文件）、OpenAPI与C/D示例一致性通过。
- 已实际查看项目页、研究页与移动截图，保留五张原截图到忽略目录`output/sprint-20261026/before/`。
- 优化顺序：上传文件清理异常不能改变已知提交结果；生成中编辑不能被草稿覆盖；上传在完整读文件前校验大小/头部；表单年份范围和字段定位；畸形健康响应不能导致页面崩溃；单次来源核验避免重复数据读取；最后统一视觉、查询状态说明、键盘和窄屏回归。
- 已有能力复用：四页路由、统一错误/请求编号、任务恢复、幂等核对、报告版本/引用快照、即时项目授权、三类显式工厂及失败关闭。A正式持久化/真实认证、C正文/检索生成质量、D数据库权限/SQL防护仍未验收；Mock与历史SQLite实验保留。
- 冻结路径：ports.py、AB-1、schemas.py、contracts/、schema.d.ts及依赖锁文件不得修改。后续每个检查点在本文补充实际结果。

### 检查点2–3：共享视觉与项目表单

- 统一可读CSS变量、卡片/间距、表格及长文本、可见键盘焦点、减少动画偏好、移动弹窗；保留现有布局和Element Plus依赖。
- 项目创建改为带标签的原生表单，Enter提交，空白名称就近提示，角色使用中文说明；演示身份声明保留。
- 检查点2类型检查通过；构建结果见最终记录。项目字段交互将在新增浏览器用例和全套回归实际验证，未以样式修改代替视觉验收。

### 检查点4–6：文献、研究与查询页

- 上传在完整文件读取/哈希前检查空文件、20 MiB上限及5字节头；页面显示文件大小及就近错误，异步选择防迟到覆盖，服务端仍负责最终验证。
- 文献状态增加中文文字标签；两页年份倒置禁止无效读取；研究页显示结果数、编辑字符和引用数、无来源说明。
- 生成草稿期间修改正文/标题/版本时，迟到结果暂存于页面内存，不覆盖当前内容；用户可载入或保留。明确切换历史任务仍沿用原行为；未将正文存入浏览器恢复存储。
- 查询页增加执行状态文字与只清除本地展示的操作，不重新提交查询、不清审计。Mock executed明确说明未执行真实SQL。
- 实际新增浏览器7项全部通过（字段键盘提交、超大/非PDF替换、两页年份、迟到草稿、查询成功/失败/拒绝及清除）；检查点3–6类型检查通过。没有新增公共API或修改幂等协议。

### 检查点7–8：可访问性、窄屏与后端可靠性

- 新增跳转主要内容的键盘入口、项目导航标签、输入字段标签、减少动画偏好；390/768/1440px分别验证四页、长标题、长正文、键盘跳转、页面宽度及无脚本/控制台错误，3项通过。保存12张截图到`output/sprint-20261026/after/`，已查看桌面研究页、手机文献及审计页。
- 故障注入实际复现3项清理问题：幂等重放错误变503、重复上传409变500、已知回滚503变500。修复仅清理本请求已确认未采纳的文件，失败只记请求编号和待对账事件，不改变已知业务结果；未知提交仍保留文件，不自动删除。孤立文件清理策略仍待A/B协商。
- 单次来源校验按论文批量读取并用精确字段索引匹配；本次检索只读取一次同一论文元数据，不跨请求缓存。锻造页码/正文/synthetic仍拒绝。数据层最终提交时的授权/约束不变。
- 新后端8项加原API测试合计54项通过，0失败/0跳过。新增测试初次有一处Context导入和一处演示用户fixture名称错误，按实际代码纠正；故障复现失败未屏蔽、未降低断言。

### 检查点9–10：端到端与异常回归

- 完整Mock闭环（登录→项目→上传→任务→检索→问答/来源→草稿→报告版本→查询→审计）原用例继续通过；任务刷新、未知提交和迟到worker沿用既有机制。
- JSON客户端将正文读取纳入同一超时边界，畸形/不完整健康数据和HTTP 200错误信封明确失败并保留请求编号；401响应体中断仍按已收到的拒绝清除身份，不伪造成成功或Mock。
- 后端全套164通过、37既有跳过（31历史实验opt-in、6正式库门槛）、0失败；Ruff及57文件格式检查、OpenAPI与C/D执行示例一致性通过。
- 首次全套浏览器53通过、0跳过/失败；截图细化和报告未保存标记后，新增用例14通过。后续补充allowed/not_executed及401正文中断的最终回归数见检查点12。
- 历史SQLite provider共享测试41通过；历史实现/进程重启31通过，均使用隔离临时库，不代表A正式数据库恢复或RLS能力。真实AI、真实SQL安全、正式认证未运行/未验收。
- 静态复核路由每次经current_user/project_context及D.authorize；共享用例证明匿名/资源替换/成员撤销被拒绝、GET任务不生成AI、D故障及关键审计失败不走controlled_statistics备用路径。授权和数据库内部约束未用前端隐藏代替。

### 检查点11：最终视觉与启动/部署

- 复核截图后使跳转链接在未聚焦时裁切隐藏，避免长页面全屏捕获出现离屏链接；文献超长标题改为表格省略+详情读取全文，并在三个视口实际打开详情。
- 报告显示未保存的编辑，明确已保存版本与后续正文变化；失败仍保留内容。正文只在当前页面内存，离开/刷新仍可能丢失未保存编辑，不宣称恢复了未保存正文。
- `.env.example`、两个Dockerfile、compose.yaml、Vite代理及两个PowerShell启动脚本均已检查；没有独立frontend/.env.example或nginx配置，不按不存在的文件说明启动。
- 现有脚本/配置不需修改；默认Mock启动和API代理由Playwright隔离Uvicorn/Vite实证。真实绑定仍显式注册，没有自动回退。环境仅取进程变量，不自动加载.env。
- Docker Compose配置解析通过；Docker Desktop Linux引擎管道缺失，容器build/up、干净环境安装未运行，不报告部署通过。pip check通过；锁文件未改，未安装依赖。
- `.gitignore`已覆盖依赖、上传、缓存、日志、SQLite本地数据、构建和截图输出，本轮无需新增忽略规则；既有历史源码/测试全部保留。

### 检查点12：最终冻结、改动清单与交接

实际工作区为`D:/2026-2027-1/数据库/数据库大作业`，分支`feat/b-integration`、HEAD `85fe82ddd51f74d03bd1137dc392e3cf052968fb`未变。本次约20分钟连续执行了12个顺序检查点，未耗尽12小时预算；基线已有功能没有重复重写。本次没有提交、推送、安装依赖、迁移用户库或操作其他成员目录。

**结论：本轮B侧本机功能、兼容性和Mock集成验收通过。容器运行验证被环境阻塞，完整部署交付及正式A/C/D能力尚未通过，不报告生产系统通过。**

#### 相对本轮起点的实际改动（12个修改、3个新增）

| 文件 | 本轮目的 |
|---|---|
| backend/app/routes/literature.py | 已知提交/拒绝与非关键文件清理分离；未知提交保留文件，日志不含路径/异常正文 |
| backend/app/services.py | 单次来源精确索引、按论文读取复用，不跨请求缓存授权 |
| backend/tests/test_sprint_reliability.py（新增） | 8项清理故障、重复读取及来源锻造回归 |
| frontend/src/validation.ts（新增） | 共享PDF大小/头部及年份检查；不替代服务端验证 |
| frontend/src/composables/useTaskRecovery.ts | 仅在完整读文件摘要前复用校验，恢复字段和幂等机制不变 |
| frontend/src/api/client.ts | JSON正文超时、200错误信封拒绝、健康结构检查、401正文故障处理和request_id保留 |
| frontend/src/App.vue | 键盘跳到主要内容、导航区域标签 |
| frontend/src/pages/ProjectsPage.vue | 项目名称字段错误、标签/Enter提交、中文角色 |
| frontend/src/pages/PapersPage.vue | 选择文件异步反馈、防迟到覆盖、年份范围、中文状态、长标题详情 |
| frontend/src/pages/ResearchPage.vue | 年份、结果数、迟到草稿保护、字符/引用数、无来源及未保存提示 |
| frontend/src/pages/SecurityPage.vue | 明确执行状态文字、清除本地结果，不自动重放查询 |
| frontend/src/style.css | 统一变量、间距/对比/焦点、文本/弹窗和三个视口布局 |
| frontend/tests/sprint-quality.spec.ts（新增） | 16项浏览器验收，原40项全部保留 |
| docs/development-guide.md、本文 | 当前行为、复验命令、12检查点、限制与交接 |

此前15修改+4新增成果均保留。本轮未再改README、state.ts、Feedback.vue、SourcesPanel.vue、useAction.ts及此前4个新增文件；Git status仍会显示它们的历史未提交修改。最终工作区合计17修改+7新增，不能全部算作本轮新写。

#### 最终实际验证（命令从仓库根目录运行）

| 命令 | 退出状态/结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 0；**164通过、37既有跳过、0失败**，5.09秒；起点156通过 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sprint_reliability.py -q` | 0；**8通过、0失败、0跳过**，0.30秒；已包含全套 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts backend/tests/ai_contracts backend/tests/security_contracts -q -rs` | 0；**53通过、6正式数据门槛跳过、0失败**，0.26秒；已包含全套 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs` | 0；历史实验provider **41通过、0失败、0跳过**，4.72秒 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs` | 0；历史SQLite/隔离HTTP进程重启 **31通过、0失败、0跳过**，8.66秒 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 0；通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 0；57文件通过 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | 0；OpenAPI一致，无重新生成 |
| `.\.venv\Scripts\python.exe scripts/export_module_examples.py --check` | 0；C/D实执行Mock示例一致 |
| `.\.venv\Scripts\python.exe -m pip check` | 0；无依赖冲突 |
| `npm.cmd --prefix frontend run typecheck` | 0；通过，包含新增浏览器测试类型 |
| `npm.cmd --prefix frontend run build` | 0；5.63秒，1637模块，JS 1088.46kB（gzip 358.14kB）；既有大包提示仍在 |
| 环境变量PAPERPILOT_E2E_API_PORT=8100、PAPERPILOT_E2E_WEB_PORT=5273、PAPERPILOT_E2E_DATA_MODE=mock，`npm.cmd --prefix frontend run test:e2e` | 最终0；**56通过、0失败、0跳过**，约1.4分钟；起点40，新增16 |
| `docker compose config --quiet` | 0；配置解析通过 |
| `docker version --format '{{.Server.Version}}'` | 1；Linux引擎管道缺失；容器build/up未运行，不计通过 |
| `git diff --check`、冻结路径diff、文件摘要、AST方法计数 | 0/检查通过；DataPort26、AIPort5、SecurityPort2，冻结文件/锁文件与HEAD及本轮起点一致，原139个非忽略文件无缺失 |

上述测试有覆盖重叠，不累加为独立总数。默认37跳过与起点完全一致：31历史实验默认不运行（另行显式执行）+6正式数据库门槛。新增skip为0，未删除旧测试或降低断言。前端未配置单独的ESLint/格式脚本，本轮不宣称运行了这些检查；已执行既有TypeScript、构建、浏览器检查。

中间故障注入3项失败已修复后通过；新增fixture的Context导入/演示用户名称错误已纠正。重放用例明确允许worker在请求间推进状态，断言实体/任务身份和正文标题不变，不要求状态停在初次pending。最终无未解决测试失败。Starlette/httpx弃用提示、Vite大包提示、NO_COLOR/FORCE_COLOR提示保留，未屏蔽。

已保存5张优化前、12张优化后截图，实际查看桌面研究/查询、平板项目、手机文献/审计等。三个视口用例打开长标题详情并检查键盘跳转、页面无横向溢出、脚本异常和控制台错误；表格本身在窄屏保留内部横向滚动，关键详情已实际操作。截图只含合成演示数据。

有限静态凭据模式检查（私钥头、GitHub/AWS常见令牌格式）未发现匹配；非忽略文件中没有真实.env、数据库/日志或上传数据，不读取真实凭据文件。`.env`、本地SQLite、上传、dist、测试输出与本轮截图路径的忽略规则已实际核对。它不等于完整秘密审计。测试8100/5273结束后无监听，日常8000/5173原进程67872/48560保持不变。

#### 遗留问题与后续顺序

- **阻塞完整交付**：Docker引擎不可用，未验证容器build/up或干净环境安装；正式认证、A持久化/RLS/并发租约、C真实PDF/检索生成、D真实受限执行/关键审计未交付，本轮Mock结果不能替代。
- **主要**：文件与数据库未知提交/孤立文件对账及清理仍需A/B确认；同步项目/成员/移出操作和报告跨刷新未知保存仍沿用既有边界，不新称服务端幂等保证。未保存正文只在页面内存，离开或刷新可能丢失。
- **一般**：Element Plus整体导入产生既有约1.09MB主JS包；正式性能数据尚未测量，后续再按真实页面和网络决定拆包；当前可访问性只覆盖键盘/布局，没有完整读屏或WCAG认证。

对A/C/D：端口、公开Schema、OpenAPI/生成类型、锁文件、注册工厂、交接文档与历史实验均不变；A继续按DataBinding实现隔离持久化provider，C继续按AIPort提供有来源结果，D继续按SecurityPort交付网关。新增B测试可作为调用失败/来源/文件边界回归，无需三位成员重写模块。

下一步优先与A接入最小真实DataBinding及隔离provider，验证原任务/论文/报告跨进程恢复，同时明确文件所有权对账；随后C/D按既有入口逐一替换。Docker可用时补容器构建/启动。当前代码保持未提交状态，供审阅后再决定提交或PR，不自动进行远程操作。


## 首页与整体框架改版预览（2026-10-27，等待视觉确认）

用户已批准先完成首页/框架，浏览器确认后再改其余页面。本轮仅重做App外框、登录入口和项目总览，保留原有登录、项目创建、成员管理、退出、异常反馈及导航。新增项目切换沿用原路由，移动端抽屉与快捷导航并存。统计来自已有项目列表，不虚构文献数、活动时间或真实AI能力。Mock/注册待验收状态保持可见，完整说明可展开。

本轮相对开始时：修改`frontend/src/App.vue`、`frontend/src/pages/ProjectsPage.vue`、`frontend/src/main.ts`及本文；新增`frontend/src/components/AppIcon.vue`、`WorkspaceSidebar.vue`、`frontend/src/home-shell.css`、`frontend/tests/home-shell.spec.ts`。原有未提交文件全部保留，文献/研究/审计三个页面文件与本轮起点逐字节一致。后端、DataPort/AIPort/SecurityPort、公开Schema/OpenAPI、生成类型、锁文件、原56项浏览器测试不变，没有安装依赖或提交/推送Git。

实际检查：

- `npm.cmd --prefix frontend run typecheck`：退出0，通过。
- `npm.cmd --prefix frontend run build`：退出0，5.07秒；主JS1102.92kB/gzip363.39kB，既有大包提示保留。
- 设置PAPERPILOT_E2E_API_PORT=8100、PAPERPILOT_E2E_WEB_PORT=5273、PAPERPILOT_E2E_DATA_MODE=mock后，`npm.cmd --prefix frontend run test:e2e`：退出0，**63通过、0失败、0跳过**，约1.5分钟；原56项+新增7项。
- 新用例覆盖304/390/768/1440px首页与项目页，统计与真实API项目列表一致、页面无横向溢出/脚本异常，桌面项目切换保留研究路由、移动菜单关闭及快捷导航、模式说明展开。四页原有业务和任务恢复回归通过。
- `.\.venv\Scripts\python.exe scripts/export_contracts.py --check`：退出0，OpenAPI一致；`git diff --check`通过。
- 本轮未修改后端，因此没有重新运行后端全套，不将此前164项结果当作今天的新运行。

中间问题均明确处理：新测试首次2通过/4失败，其中3项漏算统计旁的单位文字、1项Element Plus标签同时匹配输入和列表；修正精确定位。后续5通过/1失败为点击被选择框占位层挡住，改用正常键盘选择；首次全套62通过/1失败为注册模式说明文字变更，恢复原有待验收提示，原测试没有改断言。另一次全套启动因前一测试尚未退出而端口占用，没有运行测试，不计为通过；前次正常退出后再运行，最终63项全部通过。没有删除、跳过或削弱旧测试。

截图位于忽略目录`output/home-redesign/`：四个宽度各有登录/项目页实际渲染截图。已通过应用浏览器实际查看新版登录页、使用Alice演示身份进入项目首页并检查侧边导航；仅使用原内存Mock服务，没有触碰真实数据库。桌面图作为本轮视觉确认参考，用户确认前不改其余页面内部布局。

## 文献、研究、查询与审计页面改版（2026-10-27）

用户确认首页风格后，明确授权沿用该方向改三个业务页面。本轮保留既有业务流程、端口和任务恢复实现，统一使用已批准的海军蓝/青绿框架。

- 文献：导入区、文件选择反馈、筛选列表、处理状态、详情与页码来源抽屉。保留文件校验、重复提交防护、失败重试和移出影响确认。
- 研究：问题输入与检索条件、答案及引用、来源片段、可展开的五类证据；报告编辑、保存、引用快照、导出和版本读取沿用既有流程。待核验证据不标记为已证实。
- 查询与审计：问题建议、可展开的异常演示、独立结果区、决策与执行状态、请求编号和审计筛选。结果标题固定为本次实际提交的问题，后续修改输入不会误标旧结果。时间按浏览器本地时区展示，原时间仍作为属性保留；未执行、拒绝、失败和Mock结果继续明确区分。
- 共用来源和任务状态面板仅调整展示结构，任务ID、查询/对账/重试事件及来源字段保持原样。窄屏表格保留内部滚动，页面本身没有横向溢出。

相对本轮起点146个非忽略文件，修改：`frontend/src/components/AppIcon.vue`、`SourcesPanel.vue`、`TaskRecoveryPanel.vue`、`frontend/src/main.ts`、`frontend/src/pages/PapersPage.vue`、`ResearchPage.vue`、`SecurityPage.vue`、`frontend/tests/business-states.spec.ts`和本文。新增：`frontend/src/business-workspace.css`、`frontend/tests/business-redesign.spec.ts`。其余已有未提交成果保留，无文件删除。后端、DataPort/AIPort/SecurityPort、公开Schema、OpenAPI、生成类型、锁文件、数据库配置与历史实验逐文件摘要对比均与本轮起点一致；冻结路径Git diff为空。无安装依赖、提交或推送。

实际验证（仓库根目录）：

| 检查 | 最终实际结果 |
|---|---|
| `npm.cmd --prefix frontend run typecheck` | 退出0，通过，包含新增测试类型 |
| `npm.cmd --prefix frontend run build` | 退出0，5.40秒；1643模块，JS1115.34kB/gzip367.23kB；既有大包提示保留 |
| 设置`PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=mock`，`npm.cmd --prefix frontend run test:e2e` | 退出0，**68通过、0失败、0跳过**，约2.4分钟；原63项全部保留，新增5项 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | 退出0，OpenAPI与实际Schema一致，无重新生成 |
| `git diff --check`、起点文件摘要及冻结路径检查 | 通过，无删除或未经批准的公共契约变化 |

新增测试在304/390/768/1440px分别实际执行：创建项目、空列表、键盘聚焦选择文件、上传处理、论文详情、展开来源、检索、展开证据和支撑来源、问答、草稿、保存版本、查询与审计；断言页面无横向溢出、脚本无未捕获异常、登录后的业务流程无控制台错误。另验证修改统计问题不会篡改旧结果的问题标题，展开异常演示后仍可得到执行失败结果。原任务刷新恢复、未知提交对账、权限拒绝、业务异常、报告保存失败保留编辑等用例继续通过。

过程问题如实记录：首轮旧测试62通过/1失败，原退出用例在点击后立即发请求，可能早于异步退出完成；增加等待登录页出现的断言，原401/403断言保留，复验通过。新增用例首轮2通过/4失败，原因是把登录前正常的会话探测401也算作业务控制台错误；脚本异常继续全程监听，控制台无错误检查改在登录完成后开始，不屏蔽登录后的错误；四种宽度最终全部通过。无删除测试、降低旧断言或新增skip。

16张四视口实际渲染截图位于忽略目录`output/business-redesign/`，另保存应用浏览器实际预览图。已查看桌面研究/查询和手机研究等截图，并使用现有5173页面的Alice演示身份实际操作检索、问答、草稿保存及统计查询。服务8000/5173原PID33056/26292保持不变；隔离测试8100/5273结束后无监听。

本轮仅修改前端展示和测试，因此没有重新运行Python后端全套、Ruff或容器部署，不把以前的结果当作本轮结果。没有新增前端格式工具。Mock联调不代表正式持久化、真实PDF/AI或SQL安全能力通过；此前报告的跨刷新未保存正文、正式认证、A/C/D集成及大包提示等限制仍存在。

## 默认演示数据集（2026-10-27）

用户确认扩充默认演示数据后，新增显式入口`app.demo:create_demo_app`及`start-backend.ps1 -Demo [-Port 8000]`，普通入口、工厂和原最小fixture保持不变。只有development/test与A/C/D全Mock配置可以启动演示入口；生产或任一真实模式明确拒绝。没有数据库选型、依赖安装、迁移、数据重置API或Git提交/推送。

新增“科研证据追溯演示”项目，Alice为负责人、Bob为成员、Eve保持隔离。项目包含6篇虚构文献（2021–2025年，5篇可检索、1篇处理失败）、10个合成来源片段、2份结构化证据、一份综述的2个版本（分别2/3条引用快照），及初始化期间实际沿ResearchService→MockSecurity调用产生的成功/拒绝/故障审计。片段页码是合成定位，不对应真实PDF；没有写入或解析预置PDF文件。

数据定义与受限初始化在`backend/app/demo.py`，固定项目/成员是显式Mock fixture；其余导入、任务/证据提交、报告保存及审计沿现有DataPort和服务调用。新进程重建初始样例，用户演示编辑仍不持久化，不能声称完成真实数据库恢复；单应用重复进入生命周期不重复灌入，不覆盖运行中的数据。失败任务ID在演示启动日志中，可通过既有认证GET读取，重试必须明确调用原retry端点。

研究页新增“查看已有证据”，通过统一客户端封装原有GET证据历史，显示最新已保存结果，空/错误有反馈，无POST或AI提取副作用；成功读取后定位证据区，避免多结果时看不到内容。原“提取 Mock 证据”、报告保存/版本、任务恢复及幂等语义不变。

本轮相对起点148个非忽略文件：修改`.env.example`（只加说明）、`README.md`、`docs/development-guide.md`、本文、`scripts/start-backend.ps1`、`frontend/src/api/client.ts`、`frontend/src/pages/ResearchPage.vue`、`frontend/tsconfig.json`；新增`backend/app/demo.py`、`backend/tests/test_demo_data.py`、`frontend/playwright.demo.config.ts`和`frontend/demo-tests/demo-data.spec.ts`。旧修改/测试/SQLite实验全部保留，无删除。冻结端口文件、Schema、OpenAPI/示例、生成类型、锁文件、配置类和原Mock实现与本轮起点摘要一致；独立演示入口OpenAPI与普通入口完全相等的测试通过。

| 实际命令（根目录） | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 退出0，**177通过、37既有跳过、0失败**，6.75秒；164原有+13新增 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_demo_data.py -q` | 退出0，**13通过、0跳过、0失败**；补充入口OpenAPI相等断言后再验1.25秒；与全套重叠不累加 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 退出0，通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 退出0，59文件符合格式 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | 退出0，OpenAPI一致 |
| `.\.venv\Scripts\python.exe scripts/export_module_examples.py --check` | 退出0，C/D执行Mock示例一致 |
| `npm.cmd --prefix frontend run typecheck` | 退出0，通过，独立演示配置与测试已纳入tsconfig |
| `npm.cmd --prefix frontend run build` | 退出0，最终4.85秒，1643模块；JS1116.30kB/gzip367.55kB，既有大包提示保留 |
| 设置A=mock、API=8100、WEB=5273后，`npm.cmd --prefix frontend run test:e2e` | 退出0，原**68通过、0失败、0跳过**，约2.3分钟，仍为最小fixture |
| `npm.cmd --prefix frontend run test:e2e -- --config=playwright.demo.config.ts` | 退出0，**3通过、0失败、0跳过**，最终11.3秒；独立8101/5274及临时存储；补充定位滚动后已复验 |
| `git diff --check`、起点摘要和冻结文件检查 | 通过；原148文件无缺失，冻结接口/类型/锁文件不变 |

后端37跳过全部既有：6正式数据provider门槛+31历史实验默认不运行；本轮没有新增skip，也没有重跑历史SQLite专用验收。13新增测试验证普通入口不受影响、五种真实/生产配置拒绝、六篇文献及筛选/10来源、证据读取、两版快照及续存v3、失败任务只读/明确重试、Mock审计关联及统计数量、匿名/Bob/Eve权限和重复生命周期；不把新实例重新播种等同于持久化恢复。Starlette/httpx弃用提示保留。

新增浏览器测试在390/1440px实际演示：预置6篇文献、失败状态、年份筛选、来源页码、读取既有证据（POST计数0）、证据不足、两版正文/引用数差异、查询总数6及审计；另验证证据读取空结果、503保留请求编号/不泄露内部错误、明确重读后恢复。检查页面无横向溢出/脚本异常，登录后业务无控制台错误。过程中的两轮各2项失败来自新测试定位同时匹配两条来源/两个证据字段，改为精确定位已展开的实际内容后全部通过；没有改动旧测试或减弱断言。

桌面/手机各4张截图位于忽略目录`output/demo-data/`。本机预览使用后端8001、前端5174（原8000/5173保持运行），通过`http://localhost:5174`与旧127.0.0.1站点隔离开发Cookie；仅不同端口不能隔离Cookie。已实际使用演示启动脚本成功启动，并在应用浏览器用Alice打开新项目、读取六篇文献及综述两个版本。演示启动时允许Origins明确含localhost/127.0.0.1的5174，操作均经原授权及同源校验。

本阶段仅完成默认合成数据与展示联调；真实认证、A持久化/事务恢复、C解析/检索算法、D真实SQL网关及干净环境/容器交付依然没有因此通过。

## 安全可视化与关联阅读修订

以用户确认的修订方向实施，起点为 `feat/b-integration` 的 `12d5a6df578cd8ec42f9051b8c9124c55eff89e3`，开始时工作区干净。本轮保留四页原有功能，重点为研究工作流及安全证据展示；没有提交或推送。

实际行为：选择项目文献读取对应摘录和页码；回答引用、检索来源和证据来源同步定位文献/chunk并保留来源抽屉；迟到详情响应不能覆盖新选择。chunk不存在时提示对照历史快照，不自动用另一段代替。来源可去重加入报告正文及Source快照，仍标记待人工核验；切换项目/研究路由时保护未保存编辑，确认放弃后按新项目重新读取。助手改为问答、证据、报告三个可用键盘切换的页签。草稿失败、任务待处理/处理中、状态未知均有明确下一步，不伪造解析/索引百分比。失败草稿的测试使用受控任务响应夹具，Mock草稿服务本身没有新增失败算法。

安全页的角色/访问范围来自服务端项目列表，提交查询前重新读取；这些前端检查不能替代后端授权。调用链标为示意，状态区继续区分策略和执行结果。审计新增操作者与项目，保留事件、时间、决策、执行状态及request_id。当前用户可显示姓名，其余操作者使用返回的标识，不补造姓名。没有直接SQL执行、真实认证或A/C/D算法改动。

新增离线验证报告由隔离pytest实际执行生成，包含版本、时间、未提交标记、检查代码指纹及每组计数。当前报告为18通过：匿名访问1、跨项目/成员授权11、网关故障与关键审计6；与后端全套重叠，不能累加。面板明确其不是当前服务实时探测，不能证明运行部署等同所测版本。报告缺失/格式错误时不显示通过，失败或跳过保持实际状态。真实SQL AST/受限执行与数据库隔离/恢复分别待D、A验收。

实际文件：

- 修改：`frontend/src/pages/ResearchPage.vue`、`SecurityPage.vue`、`PapersPage.vue`，`frontend/src/components/TaskRecoveryPanel.vue`、`frontend/src/main.ts`；分别接入联动、安全范围和证据面板、上传/任务状态提示及统一样式入口。
- 新增：`frontend/src/research-security.css`、`frontend/src/components/SecurityEvidencePanel.vue`、`frontend/public/security-evidence.json`、`scripts/export_security_evidence.py`、`backend/tests/test_security_evidence_report.py`、`frontend/tests/connected-workspace.spec.ts`。
- 调整既有测试：`frontend/tests/sprint-quality.spec.ts`在原未保存长文本的跨页操作中明确点击放弃；`frontend/demo-tests/demo-data.spec.ts`读取报告前选择报告页签。原功能与安全断言保留，无删除或新增skip。
- 文档：更新`docs/development-guide.md`及本文。其余源码、历史SQLite实验与共享契约保留。

| 实际命令（根目录） | 最终结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 退出0，180通过、37既有跳过、0失败，11.18秒；含新增报告解析3项及A/C/D共享契约 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 退出0，通过 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 退出0，61个文件符合格式 |
| `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` | 退出0，OpenAPI与应用Schema一致 |
| `.\.venv\Scripts\python.exe scripts/export_module_examples.py --check` | 退出0，C/D已执行Mock示例一致 |
| `.\.venv\Scripts\python.exe scripts/export_security_evidence.py` | 退出0，18通过、0失败、0跳过，生成离线报告；与全套重叠 |
| `npm.cmd --prefix frontend run typecheck` | 退出0，通过 |
| `npm.cmd --prefix frontend run build` | 退出0，4.77秒、1646模块；JS1131.18kB/gzip372.31kB，既有大包提示仍存在 |
| 设置`PAPERPILOT_E2E_API_PORT=8100`、`PAPERPILOT_E2E_WEB_PORT=5273`、`PAPERPILOT_E2E_DATA_MODE=mock`后运行`npm.cmd --prefix frontend run test:e2e` | 退出0，79通过、0失败、0跳过，2.7分钟；原68项+新增11项 |
| 同上环境，`npm.cmd --prefix frontend run test:e2e -- tests/connected-workspace.spec.ts` | 退出0，11通过、0失败、0跳过，最终33.2秒；加强迟到响应的到达/渲染等待及引用高亮断言后复验，与全套重叠 |
| `npm.cmd --prefix frontend run test:e2e -- --config=playwright.demo.config.ts` | 退出0，3通过、0失败、0跳过，13.4秒，独立8101/5274与临时存储 |
| `git diff --check`及冻结路径相对HEAD检查 | 退出0，无删除；端口、Schema、contracts、生成类型、锁文件未改 |

37个既有跳过仍为6个正式数据库provider门槛及31个默认不运行的历史SQLite实验。本轮没有真实数据库、真实AI或SQL安全验收；没有容器/干净环境部署验收。Starlette/httpx既有弃用提示保留。

新增11项浏览器用例验证：选择文献与回答引用定位、快照去重并经API保存、迟到响应、项目切换的留在/放弃两条分支、失败草稿保留编辑及旧版本、最新范围不再含成员时阻止客户端提交、缺失引用不误高亮、失败报告显示失败计数、报告缺失/无效不伪造通过、390/1440px渲染及键盘页签。异常数据由隔离浏览器夹具构造；服务端实际越权拒绝仍由后端测试覆盖，不能将客户端禁用/提示作为安全证明。

过程记录：首轮既有68项为61通过/7失败，4项为安全状态重复文本定位，3项因新增离开确认需要显式处理。新增9项首轮8通过/1失败来自连续上传尚未完成即读取列表；等待实际论文行的可检索状态后再断言，未降低任务断言。中间一次77项为71通过/6失败，5项来自新测试对包含PDF/元数据的单元格使用了错误的精确名称，另1项查询结果超时未在定向或最终全套中复现，未将其归为已确认根因。之后定向15项全部通过、最终全套79项全部通过。默认演示首轮1通过/2失败是报告隐藏于证据页签，补充显式选择报告后原版本/快照断言全部通过。没有通过删除、跳过或降低断言制造通过结果。

实际渲染截图保存在忽略目录`output/connected-workspace/`及`output/demo-data/`；桌面/手机布局及既有304/768px回归均实际运行。已在现有localhost:5174演示站点登录Alice，生成Mock回答、点击引用、打开安全页并提交统计查询，看到一致的查询/审计request_id和18项离线验证计数；保存了应用浏览器预览截图。原8000/5173和演示8001/5174服务保留。

边界仍然存在：片段/页码不是PDF具体坐标；来源加入报告不代表人工核验通过；未保存正文不跨刷新持久化，完整对话历史未持久化；预置失败论文的PaperDetail不含task_id/失败原因，已知任务的安全错误通过原任务恢复区读取，本轮不补造原因或扩展Schema。真实安全测试待D/A提供隔离环境与结果，发布前须重新生成并核对离线报告版本。

## Phase 5A：统一开发组合与 A/B 隔离库验证

基线 `main@14701d5`，开始时工作区干净。新增 `app.dev_composition:create_app`，基于既有 DataBinding 与 C/D 工厂按配置显式组合，不执行迁移、播种或 outbox 投递。初始化失败时按成功创建顺序的逆序调用可用的同步 close，关闭失败不掩盖原错误；现有 C/D 公开端口不包含独立 ready/close。A readiness 实际检查数据库迁移版本；health 报告 C/D 仅代表注册模式，不代表模型服务或真实 SQL 已验收。正式生产认证仍未实现，生产配置被拒绝。

四种模式均实际创建应用并验证健康报告：全 Mock；A PostgreSQL + Mock C/D；A PostgreSQL + RealAI + Mock D；A PostgreSQL + RealAI + D 策略。后三种在专用 PostgreSQL 容器执行，C 使用回环地址和测试配置，未请求真实模型；D 未注入执行器，验证 `allowed/not_executed` 且无结果行。另有数据层替身的八项组合/资源清理测试，不能计为持久化测试。A+C 组合校验单次 AI 操作期限短于任务租约，但不能保证多个操作合计耗时小于租约。

`scripts/test-phase5a-isolated.ps1` 启动专属临时 pgvector PostgreSQL 16 容器，随机生成仅供测试的口令和端口；在临时工作目录中由 A 的 provider 建立随机数据库，测试结束仅清理自己创建的数据库及容器。浏览器使用该容器内独立 `phase5a_browser` 数据库、临时上传目录及端口 61085/61084；迁移和开发身份初始化均明确执行，入口自身不执行。测试 PDF 由 pypdf 生成，单独验证能提取文本；C 在 A/B 流程中为 Mock，来源明确为合成。

实际验证：真实库项目和成员、论文元数据/项目关联/任务、上传同键重放、保存报告及引用快照、审计与 outbox、应用重建后任务/论文/报告、跨项目及撤权（A 现有集成套件）、未迁移库及断连拒绝启动均通过。确认回滚测试在 PostgreSQL 提交前注入异常，验证没有论文或上传文件；确认丢失测试先真实提交再注入异常，验证已采纳文件保留并用原键对账，**并未模拟真实网络断线**。浏览器真实 API 回归验证上传后显示原 task_id、页面刷新、后端停止时状态未知、重启后重新登录读取原 task_id、论文及已保存报告；后端身份会话本身不会跨重启保留。

| 实际命令 | 结果 |
|---|---|
| `.\scripts\test-phase5a-isolated.ps1` | 退出 0：隔离 PostgreSQL 业务与组合 17 通过、共享 DataPort 41 通过、浏览器 1 通过；均 0 失败、0 跳过。浏览器中停止后端阶段的代理连接错误是测试主动模拟的服务中断。 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_dev_composition.py -q` | 8 通过、0 失败、0 跳过，数据层替身。 |
| `.\.venv\Scripts\python.exe -m pytest -q -rs` | 464 通过、1 失败、86 跳过；既有 C 测试创建符号链接时 Windows 返回 WinError 1314，当前用户缺少该权限。未删除或跳过此失败用例。默认跳过项含正式数据库需显式配置、D 的隔离库需显式启用以及 31 项历史 SQLite 实验；8 项新增 Phase 5A 隔离库测试默认不运行，另在专用容器运行。 |
| `.\.venv\Scripts\python.exe -m ruff check backend scripts` | 通过。 |
| `.\.venv\Scripts\python.exe -m ruff format --check backend scripts` | 失败：11 个既有文件不符合当前 Ruff 0.17.2 格式；本次新增 3 个 Python 文件的独立格式检查通过，未格式化 A/C/D 或历史文件。 |
| `scripts/export_contracts.py --check`、`scripts/export_module_examples.py --check` | 两项通过。 |
| `npm.cmd --prefix frontend run typecheck`、`npm.cmd --prefix frontend run build` | 通过；构建保留既有大包提示。 |
| 设置 Mock 测试端口 8100/5273 后运行 `npm.cmd --prefix frontend run test:e2e` | 最终 79 通过、0 失败、0 跳过。首轮 78 通过、1 失败：原测试在保存异步完成前读取报告；增加等待版本出现的断言后复验通过。 |
| `git diff --check` 及冻结接口/Schema/OpenAPI/类型/锁文件比较 | 通过，未改公共契约、A/C/D 数据库与算法实现。 |

未验收：真实模型及 A+C 联合语料质量、D 受限执行器/正式数据库身份与 SQL 攻防、生产认证/部署、真实提交后网络断线与上传文件补偿清理、outbox 死信和保留策略。由于全量后端有一项环境权限失败、全仓格式检查有既有失败，不能报告 Phase 5A 全部验收通过。下一步先在具备符号链接权限或合适 Linux 环境复验该测试，并协调已有格式基线；随后单独处理 A+C 证据选源。
## Phase 5B：指定论文证据选源（2026-10-28）

证据任务按项目授权读取指定论文的来源，按页码和片段 UUID 稳定排序，最多传入 50 个片段、摘录总计 60000 字符；C 的更小配置仍会继续限制输入。空来源统一展示证据不足。页面提示结果可能未覆盖全文；现有响应没有实际覆盖数量或截断标识。问答和报告草稿的选源不变。

- 隔离 PostgreSQL + RealAI + HTTP 模型响应夹具 + Mock D：`backend/tests/test_phase5b_evidence.py` 实测 1 通过。构造了另一篇论文的 50 个已持久化来源，并以受控检索结果模拟目标不在 Top-50；断言证据分支不调用此检索，同时验证目标引用、幂等重放、报告快照、跨项目拒绝和模型调用期间撤权。不是实际检索排序质量测试，也不是付费真实模型测试。
- 默认后端全套：466 通过、1 失败、87 跳过。失败是 Windows 创建符号链接时 WinError 1314；真实库及历史实验的默认跳过项仍需各自的显式隔离配置。此全套与上述隔离库测试重叠，不能直接相加。
- 一次并行执行的 C 测试中，本地推理超时清理测试失败（46 通过、1 失败、1 项按命令排除）；随后与两个 B 新测试单独复跑为 3 通过。该时序不稳定现象仍需关注，不能将首次失败视为没有发生。
- `npm.cmd --prefix frontend run test:e2e -- tests/connected-workspace.spec.ts tests/evidence-coverage.spec.ts`：隔离 Mock 服务下 12 通过。`npm.cmd --prefix frontend run build`、`npm.cmd --prefix frontend run typecheck`、`scripts/export_contracts.py --check` 通过。
- 隔离库用例须将 `PAPERPILOT_PHASE5A_ISOLATED=1` 和 `PAPERPILOT_TEST_DATA_*` 指向专门创建的隔离 PostgreSQL，再执行 `.\.venv\Scripts\python.exe -m pytest backend/tests/test_phase5b_evidence.py -q --tb=short`；fixture 只在显式隔离条件下创建并迁移临时测试库。不得对开发或未知数据库使用此流程。

尚未验证真实模型质量、精确覆盖率或数据库原生权限/RLS；模型等待期间撤权只验证了受控时序，不代表所有并发时序均已证明。

## Phase 5D：PostgreSQL 与 D 策略模式的集成准备（2026-10-28）

当前 D 的 `build_security` 未注册受限查询执行器；支持的统计问题返回 `allowed/not_executed` 和 `QUERY_EXECUTION_DISABLED`，不直接执行 SQL。C 使用 Mock；A 使用专用临时 PostgreSQL，D 使用真实策略及审计代码。真实执行、受限数据库角色、执行超时与结果限制均未验收。

`backend/tests/test_phase5d_policy.py` 使用现有显式隔离 fixture 验证策略允许但未执行、拒绝、审计记录及应用实例重建后读取、项目成员撤权、网关异常无统计备用路径、关键审计失败不返回成功。`frontend/phase5a-tests/security-policy.spec.ts` 通过真实 D 策略响应验证页面区分允许未执行及拒绝。`scripts/test-phase5a-isolated.ps1` 在专用临时容器运行这些用例；不连接开发数据库，测试库由 fixture 创建并回收。

实际执行：隔离脚本的 PostgreSQL 业务及组合测试 20 通过，PostgreSQL 共享 DataPort 契约 41 通过，浏览器 2 通过；每组 0 失败、0 跳过。后续加强撤权前后的成员验证和故障时的备用路径断言后，专用隔离容器重跑新增测试 3 通过。前端 build、类型检查、Ruff 及 OpenAPI 一致性通过。安全相关默认套件 213 通过、27 跳过；27 项为需要显式启用且提供固定 A 参考检出的 D PostgreSQL 测试，不能计为真实权限验收。浏览器原重启测试主动停止后端时的连接错误属于预期中断。

D 仍需交付受限执行器、连接管理、资源限制及失败语义；A/D 需共同落实专用数据库身份、项目绑定视图或适用 RLS、审计保护与隔离验证。测试中的应用账号为隔离库管理员，不能作为最小权限或生产安全证明。没有启用真实 SQL 执行。

## Phase 5D-3：B 侧受限执行器候选（2026-10-28）

新增 `backend/app/security/restricted_executor.py`，默认工厂与生产入口没有启用。它只接收与 D 现有三种重建计划精确匹配且项目一致的请求；可信注入的单项目只读登录须先通过异步 `ready()`，每次查询重新检查权限，使用只读事务、3 秒数据库 statement_timeout、5 秒总期限和独立连接。可显式用 `candidate_security_factory(executor)` 注入 SecurityAdapter 进行测试；调用方仍需手动管理候选的 preflight 和 close，现有健康接口不检查候选。其权限检查不证明安全视图定义、所有者或登录项目绑定可信，A/D 审查前不得启用真实运行模式。

保守行数边界为达到 100 行即失败（最多返回 99 行），避免无法辨别截断；列与行的 UTF-8 JSON 预算为 256 KiB，外层 HTTP 字段和编码开销未计入，不能将此视为完整响应大小上限。错误沿用 D 当前 `QUERY_TIMEOUT` / `QUERY_EXECUTION_FAILED`，没有新增公开端口或 Schema。

实际验证：默认隔离外的受控依赖测试与现有安全测试合计 137 通过；另在专用临时 PostgreSQL、明确创建并确认项目绑定的受限测试登录下，候选真实查询及权限测试 5 通过：合法统计及审计重读、跨项目拒绝和底表拒读、拒绝管理员身份、数据库锁导致的 3 秒超时和后续恢复、100 行失败、执行后审计失败不返回成功。非法计划的拒绝及 256 KiB 列/行数据预算由单元夹具验证，后者尚未在真实数据库/完整 HTTP 响应实测。测试管理员仅用于创建隔离夹具，真实统计查询使用受限登录。应用实例重建并非操作系统进程重启。

首次真实库测试因 Windows 默认 Proactor 循环与 psycopg async 不兼容出现 1 失败、1 setup 错误；测试进程显式使用 Selector 后最终 5 通过、0 失败、0 跳过。Windows 默认应用组合尚未适配此要求。Ruff、格式、OpenAPI 检查与前端 build 通过；未运行真实候选浏览器端到端。未将候选测试扩展为 D 的完整攻防、正式角色生命周期、RLS、生产审计写身份或部署验收。

复现入口：`.\scripts\test-phase5a-isolated.ps1 -ApiPort 61585 -WebPort 61584` 会自行创建并回收专用测试容器和测试库；2026-10-28 最终运行 25 项 PostgreSQL 业务及候选测试、41 项 PostgreSQL 共享数据契约、2 项浏览器测试均通过，均 0 失败、0 跳过。浏览器测试覆盖关闭的 D 策略模式，不是候选真实 SQL 的浏览器测试。原持久化重启测试主动停服时的代理错误属于测试预期。调用本脚本不需要也不得指向现有业务库。


## Phase 5D-4：候选运行时与限制验证（2026-10-28）

默认关闭真实执行；仅测试环境显式配置可注册候选。`app.selector_server` 在 Windows 为实际服务器进程创建 Selector 循环；单独的子进程验证 psycopg 在默认 Proactor 的拒绝行为。候选启动预检、故障后不再报告就绪、清理顺序与保留原始初始化错误由隔离测试替身验证，实际候选服务由临时 PostgreSQL、专用受限账号和独立服务进程验证。该进程测试未验证 Windows 优雅停服信号。

实际命令和结果：`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 61585 -WebPort 61584` 中真实 PostgreSQL 业务/候选 26 通过、真实 PostgreSQL 共享 DataPort 契约 41 通过、浏览器 2 通过，均 0 失败/跳过。浏览器使用 A 真实 PostgreSQL、C Mock、D 策略未执行模式，并非候选真实执行浏览器验收；浏览器主动重启服务时的代理连接错误为预期。测试身份及视图是临时隔离夹具，尚需 A/D 审查。

`python -m pytest backend/tests/test_phase5d4_runtime.py backend/tests/test_restricted_executor_candidate.py backend/tests/test_dev_composition.py -q`：21 通过、0 失败/跳过。包括完整 HTTP JSON 正文限额测试（使用测试替身，非真实 PostgreSQL 大响应）、超限失败关闭、初始化失败逆序清理与健康状态、Windows 默认 Proactor 驱动错误。此前新增清理测试因替身被注册校验拒绝失败一次，已改用符合接口的替身，最终重跑通过。数据库锁超时及随后的正常查询由上述隔离 PostgreSQL 测试验证；每次查询使用新连接而非连接池复用。

扩展跨模块测试命令 `python -m pytest backend/tests/test_phase5d4_runtime.py backend/tests/test_restricted_executor_candidate.py backend/tests/ai_contracts backend/tests/security_contracts backend/tests/data_contracts -q -rs`：114 通过、1 失败、6 跳过；失败是既有 Windows 符号链接创建权限 WinError 1314，未修改测试；6 跳过是 Mock 运行下的真实数据库门槛，不算通过。该次测试先于最后新增两项单元测试。`ruff check backend scripts`、`ruff format --check backend scripts`（117 文件）、`scripts/export_contracts.py --check` 和 `npm --prefix frontend run build`（含类型检查）通过。构建报告已有大包提示。

A/D 仍须审查角色/视图所有者、项目绑定与撤权、关键审计身份及持久化、SQL 变形和多语句、资源限制；未验收正式数据库权限、生产认证、真实候选浏览器流程或完整 HTTP 大响应的真实数据库场景。不满足默认启用及正式真实浏览器安全验收条件。

## Phase 6：角色和视图预检首轮（2026-10-28）

受限执行器新增显式视图所有者和视图定义摘要核验，以及可见跨项目数据检查。摘要依据 `pg_get_viewdef(oid, false)` 的 UTF-8 内容计算；由可信 A/D 审查并从外部提供。测试从临时隔离库生成摘要用于验证机制，本身不证明视图值得信任。空视图不能证明项目绑定正确，正式所有者权限、其他数据库对象权限和项目绑定仍待 A/D 核验；执行继续默认关闭。候选隔离配置现在必须提供所有者和两个摘要，属于开发测试配置变更，公开端口和 OpenAPI 未改。

实际运行 `scripts/test-phase5a-isolated.ps1 -ApiPort 61785 -WebPort 61784`：隔离 PostgreSQL 业务与候选 31 通过、真实 PostgreSQL 共享数据契约 41 通过、浏览器 2 通过，均零失败/跳过。新增用例验证视图定义被替换、错误所有者配置、受限角色成为视图所有者、授予底表读取权、错误绑定暴露其他项目行时拒绝预检。真实执行使用临时受限登录；测试管理员仅准备隔离环境。浏览器测试仍是 D 策略不执行模式，并非真实执行浏览器验收。服务重启阶段的代理连接错误为测试有意中断。

另外，在另一个专用临时 PostgreSQL 容器中运行 `backend/tests/test_outbox_delivery.py -q -rs --tb=short`：5 通过、0 失败/跳过。此组使用管理员测试数据库身份，不证明正式审计最小权限；永久失败、死信与保留运维仍待完善。策略、审计、候选、生命周期与 SecurityPort 共享测试合计 138 通过；Ruff 与 117 文件格式检查、OpenAPI 一致性检查均通过。尚未运行本轮真实 AI、候选真实执行浏览器、真实数据库超大 HTTP 响应及 Windows 优雅停服验收；尚不满足 Phase 6 最终验收。

## Phase 6：安全查询前置拒绝审计（2026-10-28）

在已进入安全查询路由的未认证和项目授权拒绝路径写入系统级 `secure_query.access_denied`。匿名时 actor 为空；经服务端认证后记录 actor；项目始终为空，不写用户问题或未经授权的项目标识。审计不可用返回 503 且不调用网关。由于项目为空，该事件不出现在项目审计页面，系统级读取权限、保留方式及更早的 CSRF/参数验证拒绝覆盖仍需 A/D 确认；不代表正式身份认证已实现。

`python -m pytest backend/tests/test_secure_query_access_audit.py backend/tests/test_security_audit.py backend/tests/test_security_authorization.py backend/tests/test_phase5d4_runtime.py -q`：91 通过、0 失败/跳过。临时隔离 PostgreSQL 单独执行 `backend/tests/test_phase5d_policy.py -q -rs --tb=short`：5 通过、0 失败/跳过，包含已提交拒绝记录在应用重建后读取以及审计写入故障测试；使用真实 A PostgreSQL 和 D 策略，数据/审计连接为测试管理员，不证明正式数据库最小权限。

`scripts/test-phase5a-isolated.ps1 -ApiPort 61885 -WebPort 61884`：32 项真实 PostgreSQL 业务及候选、41 项 PostgreSQL 共享 DataPort 契约、2 项浏览器测试通过，均 0 失败/跳过；浏览器仍使用 D 策略未执行模式。该脚本先于最后添加的审计故障用例启动，后者由上述单独隔离运行覆盖；两组结果有重叠，不能相加。Ruff、118 个 Python 文件格式、OpenAPI 一致性、前端构建和类型检查通过。真实 AI、真实执行浏览器、生产认证和 A/D 安全联合验收本轮未运行。

## Phase 6：隔离浏览器真实查询（2026-10-28）

`scripts/test-phase6-security-browser.ps1`：1 个 pytest 入口内的 1 个 Playwright 浏览器流程通过、0 失败、0 跳过。临时 PostgreSQL 使用 A 真实持久化、D 真实策略和 B 候选执行器，查询连接为单独的受限测试身份；C 使用 Mock。浏览器验证 2020 年论文数为 1、真实执行及策略拒绝状态、请求编号和项目审计关联、服务停机反馈、服务重启后重新登录并读取审计。使用浏览器共享会话直接发起的 API 请求验证跨项目拒绝和成员撤权后拒绝，页面重载显示项目不可访问。停机测试调用进程终止，不证明优雅停服。

开发中首次和第三次运行因登录后过早导航导致测试失败，第二次因为测试期待的故障提示与页面先检查项目范围的实际行为不同而失败；修正测试等待和断言后，隔离流程连续两次通过。危险 SQL、多语句验证在后端策略测试执行，不通过当前仅接收自然语言的浏览器接口直接发送；关键审计故障、数据库超时和行数限制不在此次浏览器流程中注入。A/D 对正式角色、视图和绑定的审查、空项目绑定证明、生产认证以及 A+C+D 真实模型联调仍未完成，不得将本结果作为正式安全验收通过。

本轮相关策略、审计、候选与共享 SecurityPort 测试 142 通过、0 失败/跳过；Ruff、119 文件格式检查、OpenAPI 一致性、前端类型检查和构建通过。前端构建仍提示现有大包体积。

## Phase 6.2：独立基线与绑定核验候选（2026-10-28）

`view_baseline.py` 固定从一次性 PostgreSQL 16 中仓库安全迁移生成的视图定义摘要；运行时必须与该基线一致，非 16 主版本拒绝。基线及生成链仍待 A/D 审核，不能因数值固定即称可信。隔离测试显式安装独立 `002_binding_verifier.sql`，其 SECURITY DEFINER 函数仅判断 SESSION_USER 和指定项目的绑定；固定搜索路径、函数体、所有者、公开 EXECUTE 与调用权限均检查。测试视图/函数所有者为新建不可登录身份，受限登录独立于测试管理员。应用不会自动安装安全迁移，执行仍默认关闭。

隔离测试覆盖两项目的独立受限身份、重复绑定、空项目正确绑定与空项目缺失/错误绑定、视图定义和 security_invoker 变更、所有者写权限、reader/owner 的 schema 创建权限、函数体篡改和 PUBLIC 授权。受限角色与不可登录所有者的业务表权限进行有限白名单检查；函数、扩展、序列、授予其他账号的函数 EXECUTE 权限及所有数据库对象权限未全面验证。绑定校验和查询是不同语句，并发撤权窗口尚未消除。A/D 尚未确认正式迁移、基线、函数所有者与 ACL，不应在正式环境启用。

`scripts/test-phase5a-isolated.ps1 -ApiPort 62285 -WebPort 62284`：隔离 PostgreSQL 业务及候选 41 通过、PostgreSQL DataPort 共享契约 41 通过、浏览器策略未执行模式 2 通过，均 0 失败/跳过。`scripts/test-phase6-security-browser.ps1`：真实候选执行的隔离浏览器 1 通过。相关策略、审计、候选和 SecurityPort 测试 131 通过。Ruff、120 个 Python 文件格式检查、OpenAPI 一致性、前端类型检查及构建通过；构建保留大包提示。

开发中有两轮隔离失败：一轮运行期间代码变化导致子进程启动失败；另一轮新权限查询的 psycopg 百分号转义错误导致 12 个候选夹具 setup 错误（23 通过），已修正并重新运行上述最终测试。浏览器服务主动中断阶段的代理连接错误属于测试场景。以上结果均非 A/D 正式安全验收；生产认证、完整函数权限和并发撤权仍未验证。

## Phase 6.2-B：依赖、角色继承和绑定再验证（2026-10-28）

候选预检增加视图关系依赖集合、绑定表主键和已验证外键、允许视图的列级写授权、非系统序列权限、额外非系统 SECURITY DEFINER 可执行权限检查，并要求受限登录和所有者 NOINHERIT。基线仍由版本控制中的 PostgreSQL 16 摘要提供，代码未动态信任被检数据库摘要；A/D 对基线和正式权限的独立审查尚未完成。

执行过程中、用户后续补充禁止删除数据的约束之前，`scripts/test-phase5a-isolated.ps1 -ApiPort 62385 -WebPort 62384` 在临时容器运行了 PostgreSQL 业务/候选 46 通过、共享契约 41 通过和浏览器 2 通过，均 0 失败/跳过。补充约束后，仅选择不执行测试数据删除的用例，在新建临时 PostgreSQL 容器中运行 `test_restricted_executor_postgres.py` 的定向组：21 通过、9 未选入；随后单独复测空项目正确绑定、未绑定、绑定改指其他项目后的下一次查询及真实受限查询：4 通过、27 未选入。两组有重叠，不相加；其余未选入项不是通过或 pytest 跳过。绑定改指其他项目撤销了原项目对应关系；受本轮约束，没有另行执行直接删除绑定行的测试。每次只清理本次创建的隔离测试数据库和容器。

相关策略、审计、候选、生命周期和 SecurityPort 测试 139 通过、0 失败/跳过；Ruff、120 文件格式和 OpenAPI 一致性检查通过。对 PostgreSQL 内置/普通函数、扩展、其他账号 ACL、底层表所有权、预检连接的 search_path、并发撤权窗口及生产身份的完整审查仍未完成；不将结果视为正式安全验收，默认执行保持关闭。

## Phase 6.2-B：搜索路径和绑定变更复验（2026-10-28）

后续在候选预检和查询的只读事务内固定 `pg_catalog, public` 搜索路径，查询返回前再次检查绑定。新建隔离 PostgreSQL 中定向执行 `test_restricted_executor_postgres.py`：7 通过、26 未选入，0 失败/跳过；包含角色预设搜索路径被覆盖、预检后绑定改指时结果不被报告为成功、两个项目与空项目、未绑定及连接不可达。只清理本次测试资源；未通过此测试证明所有并发时序，最后一次绑定核验之后仍存在窗口。

相关候选、策略、审计、生命周期和 SecurityPort 测试 139 通过、0 失败/跳过；Ruff、120 文件格式及 OpenAPI 检查通过。真实 SQL 默认关闭；内置/普通函数、扩展、其他身份授权、基线和生产配置仍需独立安全审查。浏览器和真实 AI 联调本轮未运行。

## Phase 6.2：绑定与结果同一语句快照（2026-10-28）

仅对已匹配 D 现有三种可信模板的计划，候选执行器使用固定包装，将绑定判断与统计结果置于同一 PostgreSQL 语句快照；空结果和错误绑定以标记区分，并按原模板的年份、主题或论文标识排序。结果返回前继续执行一次绑定复核。SQL 包装没有修改公开端口或策略允许清单；最后一次复核后仍可能发生并发变更，不能将其视为完整即时撤权保证。

临时隔离 PostgreSQL 定向执行候选测试：11 通过、23 未选入、0 失败/跳过；包括三种统计计划、多年份和论文排序、空项目、两项目身份、绑定变更、连接不可达、数据库超时和 100 行边界。仅清理本次隔离资源，未触碰现有业务库。相关候选、策略、审计、生命周期及 SecurityPort 测试 139 通过；Ruff、格式和 OpenAPI 一致性通过。此轮没有运行真实 AI 或浏览器联调。视图基线、正式角色与函数权限的独立安全审查和完整并发撤权仍未完成，真实执行默认关闭。

## Phase 6.3：隔离执行及响应边界补测（2026-10-28）

临时 PostgreSQL 16 容器中，管理员仅准备迁移、项目、角色和合成数据；统计查询由专用受限登录执行。`test_restricted_executor_postgres.py -k 'not empty_project_binding_is_verified_and_missing_or_wrong_binding_refused and not binding_table_losing_primary_key'`：34 通过、2 未选入、0 失败/跳过。新增前置审计故障测试确认执行器未被调用且恢复审计后查询可执行；已有后置审计故障测试确认查询已执行时仍不返回成功。真实数据库超大响应测试构造结果数据低于执行器预算、完整 JSON 超过 256 KiB 的边界，实际经 FastAPI 返回 503，并保留查询已执行的关联审计。该 HTTP 测试使用 TestClient，并非网络浏览器测试。首次使用单条超长主题时 PostgreSQL 索引项限制使测试数据插入失败；改为两条合成主题后通过。

隔离容器中 `data_contracts/test_real_database.py --data-contract-provider app.data.provider:build_harness`：6 通过、0 失败/跳过，验证 PostgreSQL 重连、并发导入、版本冲突、领取和租约恢复。`phase6_browser_check.py::test_phase63_restricted_read_only_browser`：1 个 pytest 测试通过，内部运行 1 个真实浏览器流程，使用真实 PostgreSQL、D 策略和受限身份，C 为 Mock；覆盖合法结果、拒绝、服务中断、重启后审计与非成员拒绝。进程终止不证明优雅停服，未在此浏览器流程执行成员撤权。

本轮候选、认证审计和 SecurityPort 测试 105 通过；策略测试另 118 通过。默认共享契约运行结果为 104 通过、1 失败、6 跳过：Windows 未赋予创建符号链接的权限导致 AI 文件链接测试失败；6 项真实数据库门槛在 Mock provider 下跳过，已由上述真实数据库独立运行覆盖。Ruff、120 文件格式、OpenAPI 一致性、前端类型检查及构建通过；构建仍有大包体积提示。

预检仍不是全数据库权限审计：普通或内置函数、扩展、其他身份 ACL、底表所有者及生产身份尚未完整验证；视图哈希仅固定 PostgreSQL 16，需独立复核迁移和基线。绑定最终复核后仍有并发撤权窗口。真实模型、本轮 Windows 默认 Proactor 的兼容性与 selector_server 优雅停服、正式认证和正式环境部署未验收。安全执行保持默认关闭，本轮不宣称正式安全验收通过。

## Phase 6.4：Outbox 租约和死信首轮（2026-10-28，部分完成）

环境：Windows、Python 3.12.6、Node 24.14.1、专用临时容器 PostgreSQL 16.15。运行 `powershell -NoProfile -File scripts/test-phase64-isolated.ps1 -Browser`：数据库相关测试组 25 通过、0 失败/跳过（其中 CLI 就绪失败一项使用测试替身）；浏览器入口 1 通过、0 失败/跳过。脚本创建独立容器、随机端口与临时数据库，使用管理员测试身份进行迁移、业务写入和审计投递，因此不能证明业务或审计最小权限；浏览器安全统计使用单独受限身份、真实 D 策略，C 为 Mock。数据库组覆盖版本 1 到 2 的迁移与重复校验、并发领取、旧领取确认保护、模拟确认未知后按 event_id 重投审计、死信、业务事务回滚、上传未知提交对账、应用实例重建和任务租约契约。服务进程和独立投递器重启、真实断网与完整权限隔离未由此证明。

本轮实际执行 `python -m pytest backend/tests/test_task_recovery.py backend/tests/test_security_audit.py backend/tests/test_secure_query_access_audit.py backend/tests/security_contracts -q -rs`：47 通过、0 失败/跳过，使用 Mock 和测试替身。`python -m ruff check backend scripts`、`python -m ruff format --check backend scripts`（120 文件）、`python scripts/export_contracts.py --check`、`npm run typecheck`、`npm run build` 均通过；构建有既有大包警告。测试命令中的 `python` 为仓库 `.venv`，npm 命令在 frontend 下执行。以上组可能与历史测试重叠，不作为相加总数；完整共享契约和完整浏览器业务回归本轮未运行。

迁移 002 只增加最后尝试时间和死信时间及索引，不删除事件。业务启动不自动迁移；显式升级后旧版本不能安全理解死信，回退需停止投递并经备份恢复方案处理。默认 30 秒租约和 5 次失败后死信，原事件和错误类别保留，无自动清理。死信授权重投及完整历史、独立投递器健康与告警、审计追加专用身份和系统级读取、真实连接中断及进程崩溃恢复、任务完整执行期限和 AI 调用未知结果仍未完成。不得把 Mock 故障模拟、测试管理员写入或事件 ID 去重当作生产权限、真实断线或所有下游副作用恰好一次的证明。

## Phase 6.5：凭据与会话首轮（2026-10-28，部分完成）

Windows、Python 3.12.6、Node 24.14.1、隔离 PostgreSQL 16.15：`powershell -NoProfile -File scripts/test-phase65-isolated.ps1 -Browser` 真实数据库认证与 HTTP 组 2 通过、0 失败/跳过；真实浏览器入口 1 通过、0 失败/跳过。随机测试密码由进程环境提供，不写仓库。数据库业务和认证连接使用测试管理员，不能证明最小权限；浏览器使用真实 PostgreSQL 会话，C/D 为 Mock、安全 SQL 关闭。覆盖密码、非法身份、数据库摘要、跨实例、空闲过期、停用、重置撤销、可信 Origin、CSRF、刷新和退出；并不覆盖所有部署与安全场景。

`powershell -NoProfile -File scripts/test-phase64-isolated.ps1 -Browser`：数据库及回归测试组 25 通过（含一项测试替身）、浏览器入口 1 通过，均 0 失败/跳过。首次运行该回归时浏览器 1 项失败：身份恢复后按新逻辑返回原目标项目，测试仍等待项目列表；更新断言以验证项目权限与原审计后重跑通过。此浏览器测试使用受限统计身份与真实 D 策略，但认证为开发身份。`python -m pytest backend/tests/test_task_recovery.py backend/tests/test_security_audit.py backend/tests/test_secure_query_access_audit.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：82 通过、0 失败、6 跳过，跳过项为默认 Mock 下真实数据库门槛。`npx playwright test tests/auth-session.spec.ts --reporter=line`：5 通过，认证响应使用测试替身，涵盖三个视口、403 和迟到响应。

Ruff、124 文件格式、OpenAPI 一致性、前端类型检查及构建通过，构建仍有大包提示。生成 OpenAPI 和 TypeScript 包含新增密码输入、CSRF 路由和认证模式标识，属于必要公开契约扩展。生产启动仍被禁止；生产 TLS/Secure Cookie、认证限速、独立认证数据库最小权限、会话到期/停用审计、登录退出审计与会话事务一致性、完整 A+C 联调和所有浏览器异常情形尚未验收；不能报告正式认证验收通过。

## Phase 6.6：最终集成检查（2026-10-28，未通过）

最终状态为 **未通过正式交付**：生产配置目前主动拒绝启动，正式身份、数据库最小权限及真实 SQL 的全部前置条件未验收；干净环境后端容器构建失败，A+C+D 正式闭环未完成。保留默认关闭真实执行，不能把测试身份及 Mock 结果作为生产安全证明。

| 模块 | 本轮实际证据 | 未完成或风险 |
| --- | --- | --- |
| A 数据层 | 隔离 PostgreSQL 16，显式迁移；业务/认证写入使用测试管理员，受限统计使用专用受限角色。 | 正式业务、认证、审计角色和系统级审计读取权限未验证；基线及并发撤权需独立安全复核。 |
| B 业务与前端 | Playwright 默认配置 85 通过，覆盖 Mock/替身页面、项目、论文、研究、报告与审计；凭据真实库浏览器另行测试。 | 不能据此证明真实四方全链路、进程级故障恢复及生产会话安全。 |
| C AI | DeepSeek `deepseek-flash` 一次真实聊天成功、0 失败，无重试；FastEmbed `BAAI/bge-small-zh-v1.5` 512 维、本地嵌入。虚构单页 PDF 的三种检索均命中第 1 页；回答 12 篇及引用与该页一致，人工核对一例。 | 实际本轮语料是进程内虚构材料；无真实数据库 A+C+D 完整链路、整体证据质量及科研真实性验收。`synthetic=false` 不代表真实科研结论。 |
| D 安全 | 隔离库真实策略和受限身份的查询、拒绝、超时、行数、完整 HTTP 响应大小与审计故障见本轮隔离测试。管理员仅用于测试初始化；统计通过受限登录。 | 候选仅允许 test，正式角色、视图所有者、函数/扩展权限、绑定和撤权窗口未完整审查；不启用生产执行。 |
| 部署 | Compose 配置解析、前端容器独立启动和本机 Mock 服务健康检查通过。 | 后端镜像构建失败，Compose 联合启动、生产 TLS、干净环境与优雅停服未通过。 |

本轮命令和边界：Windows、Python 3.12.6、Node 24.14.1；Docker 使用临时 PostgreSQL 16.15。`python -m pytest backend/tests -m 'not real_database and not legacy_persistence' -q -rs --tb=short`：490 通过、1 失败、0 跳过、139 未选入；失败为 Windows WinError 1314 无权限创建符号链接，本轮不降低断言。初次此组另有四项 CSRF 示例、认证模式配置或报错文字不一致；已补契约示例与显式配置并修复，最终仅剩上述环境失败。测试使用本机 `.venv`，真实数据库另行隔离运行。`npx playwright test --reporter=line`：85 通过，使用现有 Mock/测试替身；`npm run typecheck` 和 `npm run build` 通过，构建有大包警告。

`powershell -NoProfile -File scripts/test-phase65-isolated.ps1 -Browser`：真实 PostgreSQL 认证测试 3 通过，真实浏览器入口 1 通过，0 失败/跳过；认证/业务写入为隔离测试管理员，C/D 为 Mock。`powershell -NoProfile -File scripts/test-phase64-isolated.ps1 -Browser`：27 项数据库及测试替身回归、1 项真实受限查询浏览器入口通过，0 失败/跳过；两组存在测试重叠，不相加。`python -m pytest backend/tests/test_phase5b_evidence.py -q -rs --tb=short` 在单独临时 PostgreSQL 中 1 通过，模型为受控 HTTP 夹具；`python -m pytest backend/tests/ai_contracts/test_ai_port.py --ai-contract-provider ai_contracts.real_provider:build_harness -q -rs`：10 通过，模型为替身、数据为内存。

真实模型调用由一次临时脚本完成，本轮只发起一次、未重试；事后整理的 `scripts/phase66_ai_smoke.py` 是可复现入口，需将本地安全配置注入进程后显式加 `--live-model`，脚本不会读取 .env，也未再次调用。来源预算维持 50 条及 60000 字符，证据编号有效不等于内容已核实。

`docker compose config --quiet` 通过；`docker compose build` 因 BuildKit Windows sharedkey 错误失败；`$env:DOCKER_BUILDKIT='0'; docker build -t paperpilot-phase66-local-backend .` 因容器 DNS 无法解析依赖源失败。相同传统构建方式下前端镜像构建及临时容器 HTTP 200，但未与后端联动。Ruff 检查通过、125 文件格式检查通过（本机有缓存写入权限警告）；`python scripts/export_contracts.py --check` 和 `git diff --check` 通过。

本轮修正：开发 Compose 显式认证模式和健康依赖；新增 CSRF 契约示例和生成逻辑；旧 D 测试显式设置开发认证；登录前置审计改为 pending，避免会话创建失败时虚报已登录；反复领取后崩溃的 Outbox 事件达上限保留并标记 `DELIVERY_OUTCOME_UNKNOWN`。该状态须按 event_id 对账，尚无授权重投和告警。认证成功终态审计与会话更新仍不在同一事务；后台任务完整 AI 调用期限、死信运维、系统级审计授权以及正式部署权限均为未解决阻塞。

`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 62985 -WebPort 62984` 最终退出 0：隔离 PostgreSQL 业务、策略及受限执行器测试 58 通过，共享 PostgreSQL DataPort 契约 41 通过，浏览器 2 通过，均 0 失败/跳过；浏览器分别为 A 真实库加 Mock C/D，以及 A 真实库加真实 D 策略的未执行模式。首次运行前两组 58 和 41 通过，但 Windows PowerShell 引号处理导致测试 PDF 创建失败，浏览器未运行；修正脚本并完整重跑后上述三组通过。重启故障场景中代理连接中断是测试主动停服造成。业务连接使用临时数据库管理员身份，不能证明生产最小权限；受限执行器测试使用单独测试角色。各测试组有重叠，不加总。

## Phase 6.2–6.6：持续修复与复验（2026-10-28，尚未最终通过）

本轮继续修复：凭据会话登录/退出终态审计与会话操作同一 PostgreSQL 事务；新增数据库共享登录限速和已知失效会话审计；任务提交与领取串行化并拒绝过期租约，worker 有总体等待期限；Outbox 未知结果需对账后才能显式重投，保留事件 ID、递增尝试次数及不可变重投历史；存储故障后的独立投递循环重试；健康接口报告已知 worker 故障。新增迁移 004–006，均需显式对确认的数据库迁移，启动不自动迁移。限速使用 ASGI peer，反向代理可能改写或共享该地址；未知外部调用结果并不能由本地超时判断。事件去重不等于外部副作用端到端恰好一次。

独立 PG16 角色测试：受限统计身份或视图所有者被授予服务器文件函数权限时拒绝就绪；执行器测试 38 通过。审计角色测试 11 通过：专用业务测试身份可追加审计、不可直接读取/更新/删除审计底表，系统事件不进入项目视图；单独 NOLOGIN 所有者及显式 `scripts/check_audit_boundary.py` 预检。该视图仍允许共享业务身份查询不同项目事件，项目之间的访问控制依赖服务端；默认迁移所有者不是受审查的生产角色。预检必须显式运行，尚未纳入生产启动；函数、序列和扩展的全部权限未证明。管理员仅准备隔离库、角色及种子；上述受限行为使用专用身份。

隔离 PostgreSQL 16.15 最终迁移版本 6：`powershell -NoProfile -File scripts/test-phase64-isolated.ps1 -Browser`：32 通过、浏览器入口 1 通过，0 失败/跳过；含真正终止测试 worker 进程、人工将租约置过期后新进程恢复、投递器进程恢复、死信重投和事务回滚。租约过期采用数据库时间注入而非等待真实时长；业务和投递连接为测试管理员，部分故障由测试替身模拟。`powershell -NoProfile -File scripts/test-phase65-isolated.ps1 -Browser`：6 项认证数据库测试、1 项真实浏览器入口通过，0 失败/跳过，数据库业务身份为测试管理员。`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 63185 -WebPort 63184`：60 项真实 PostgreSQL/策略和受限执行器测试、41 项真实 PostgreSQL DataPort 契约、2 项浏览器测试通过，0 失败/跳过。期间一次浏览器回归旧断言等待项目首页，但新登录恢复原项目；更正断言验证原项目与原任务后重跑通过。故障测试中主动停服产生的代理连接错误不是生产故障。

真实模型联调使用虚构 PDF、本地 BGE FastEmbed、隔离 PostgreSQL、凭据会话和真实 D 策略，SQL 始终关闭，数据库业务连接为测试管理员。三轮独立评测各调用聊天问答 1 次和证据提取 1 次，合计 6 次、无自动重试。前两轮严格检查失败：五个证据字段无引用，不能认定选源缺陷；在澄清提示中的虚构内容边界后第三轮 1 通过，五字段均待核验、每项引用与数据库 paper_id/chunk_id/页码/摘录一致，字段包含 fictional/simulated 限定，报告快照及实例重建重读通过。这里只对标识和限定词做自动匹配，尚未人工逐项核对语义支持；提示不能保证未来输出的限定，不能作为真实科研结论或模型整体质量验收。前三轮结果均保留；SQL 真实执行和 A+C+D 生产验收未因此通过。

Windows 下符号链接测试仍因 WinError 1314 失败；只读挂载工作区的隔离 Linux 容器运行同一测试 1 通过。Linux 联调镜像执行 `python -m pytest backend/tests -m 'not real_database and not legacy_persistence' -p no:cacheprovider -q -rs --tb=short`：493 通过、1 跳过（仅 Windows Proactor 探针）、160 未选入。最初在 Mock 后端镜像执行时有 11 项收集错误，原因是该镜像未装 PostgreSQL 驱动；改用包含锁定驱动的隔离联调镜像重测。镜像及 Compose 在临时 Docker DNS 映射下可构建，Mock 开发模式完整容器启动和重启通过；单独真实组合开发镜像在隔离 PostgreSQL 上验证凭据登录、真实 C 注册及 D 策略，未执行外部模型或 SQL。默认容器 DNS、生产 TLS、生产身份和真实 SQL 尚未通过部署验收。

本轮最终前端 `npx playwright test --reporter=line`：85 通过、0 失败/跳过，主要使用 Mock 或路由替身；`npm run typecheck`、`npm run build`、OpenAPI 检查、Ruff 和 130 个 Python 文件格式检查通过。新认证限速在登录路由新增 429 契约响应，并同步生成 OpenAPI 和 TypeScript。构建保留现有大包警告。历史 SQLite、原测试及未提交成果未删除；未提交或推送 Git。

## 安全边界继续复验（2026-10-29，尚未通过生产验收）

新增显式审计预检配置 `PAPERPILOT_AUDIT_PREFLIGHT=required` 与独立提供的 `PAPERPILOT_AUDIT_TRUSTED_OWNER`：统一应用入口在启动、健康检查和安全查询之前检查业务身份、视图与审计所有者权限；检查失败关闭，不调用网关。默认 `disabled` 仅保留开发及隔离测试用途，生产模式仍拒绝启动。预检与执行分属不同事务，不能消除并发 DDL/权限变更窗口；生产管理身份及权限变更仍须单独管控。共享业务身份的审计视图包含所有项目事件，项目间隔离仍依赖服务端授权，不是数据库原生隔离。

真实隔离 PostgreSQL 16.15：`powershell -NoProfile -File scripts/test-phase6-security-browser.ps1` 的 Python 测试入口 4 通过、0 失败、0 跳过，使用真实浏览器、真实 D 策略、独立受限统计身份；新增场景另使用审计受限业务身份、受信 NOLOGIN 所有者和 required 预检，管理员仅准备隔离环境，C 为 Mock、使用开发身份。验证真实统计结果、审计关联、策略拒绝、跨项目拒绝和服务恢复。该业务测试身份对非审计业务表授权较宽，不证明生产最小权限。首次运行旧入口误执行需要另一个夹具的认证浏览器，2 通过、1 失败；修正测试文件选择后重跑如上。受限查询预检的序列权限检查采用 CASE 按对象类型求值，并在存在无授权额外序列时验证。

`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 63285 -WebPort 63284`：61 项真实 PostgreSQL 业务/策略/受限执行器测试、41 项 PostgreSQL 共享 DataPort 契约、2 项浏览器测试通过，均 0 失败/跳过。隔离环境使用管理员准备及一般业务连接；统计执行测试使用单独受限身份。重启测试中主动停服产生的代理连接中断不代表正常运行故障。测试组之间存在重叠，不相加。

审计角色隔离 PG 测试最终 17 通过、0 失败/跳过：包括多余服务器文件函数、未经审查的 SECURITY DEFINER、所有者序列授权拒绝，以及启动后实际权限漂移阻断网关；普通无授权序列不影响合法预检。`.venv\Scripts\python.exe -m pytest backend/tests/test_audit_startup_preflight.py backend/tests/test_dev_composition.py backend/tests/test_data_binding.py backend/tests/test_secure_query_access_audit.py backend/tests/test_database_pool_shutdown.py backend/tests/test_restricted_executor_candidate.py -q`：40 通过；其中启动和连接池生命周期包含测试替身，不能作为数据库权限证明。数据库连接池关闭后拒绝新连接、唤醒等待者，并关闭停服期间才建立完成的连接。

`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1`：隔离 PostgreSQL 16.15 上 18 项测试通过、0 失败/跳过（含上述 17 项角色测试，不重复相加）。另外一项候选测试使用不同项目的专用登录，验证数据库 `SESSION_USER` 绑定、不同项目只读各自审计、错误或缺失绑定、重复绑定、绑定撤销、用户停用、成员权限以及底表和绑定表不可读取。普通 GUC 和角色切换不能扩大读取范围；撤销后的测试使用 READ COMMITTED 新语句，不证明旧事务快照立即失效。管理员只准备本次隔离环境。候选 SQL `backend/app/security/migrations/003_candidate_scoped_audit.sql` 不由现有数据迁移器自动执行，也未接入 DataPort：仍需独立可信定义/所有者预检、项目与用户凭据供应和轮换、隔离连接池及运维，不能描述为现有业务审计已具备数据库原生项目隔离。

只读挂载工作区的 Linux 联调容器执行 `python -m pytest backend/tests -m 'not real_database and not legacy_persistence' -p no:cacheprovider -q -rs --tb=short`：503 通过、1 跳过（Windows Proactor 专属探针）、167 未选入。Windows 下 `python -m ruff check --no-cache backend`、`python -m ruff format --check backend`（最终 117 文件）、`python scripts/export_contracts.py --check`、前端 `npm run typecheck` 与 `npm run build` 通过；构建仍提示大包。`npx playwright test --reporter=line`：85 通过、0 失败/跳过，为 Mock 与替身前端回归。上述结果均为本轮运行，不代表真实模型、正式生产部署或完整权限验收通过。

## Phase 6.7：受限审计读取联合验证（2026-10-29，部分完成）

统一组合入口增加默认关闭、仅 `test` 环境可启用的单用户/项目审计读取：凭据会话提供的用户和服务端项目上下文必须匹配独立配置，专用 PostgreSQL 登录由绑定表及 `SESSION_USER` 确定数据库可见范围。每次读取采用新建只读事务连接，检查版本控制中的候选迁移和 PostgreSQL 16 固定搜索路径下的视图摘要、绑定函数、角色属性与受限权限，随后读取该项目审计。业务认证/授权仍在服务端重新检查；普通成员及非成员不得读取负责人审计。配置错误、绑定撤销或读取不就绪时拒绝，不使用共享视图作为备用路径；安全查询也先检查审计读取是否可用。统一入口启用候选时强制业务审计预检，并要求业务身份没有旧项目审计视图的 SELECT 权限。候选沿用既有 003 SQL，未加入自动数据迁移，不会自动配置角色。

信任边界：当前只有一组固定映射，凭据通过受保护的进程环境注入，未实现多用户凭据分发、轮换/撤销服务；部署时需独立审查 owner、绑定表写入身份、基表及全部依赖对象与其他数据库权限。摘要是从版本控制迁移在隔离 PostgreSQL 的已知搜索路径下生成并固定到代码中，尚无独立第三方审查；不从待验收数据库即取即信。读取使用同一只读事务快照，正在运行的查询不会因撤权立即取消；检查与后续 SQL 仍有并发权限和 DDL 变化窗口。系统级审计不向项目读取身份开放，系统级运维读取方案仍待完成。生产配置继续拒绝启动，真实 SQL 默认关闭。

Windows、Python 3.12.6、Node 24.14.1，测试独立临时 PostgreSQL 16.15：`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1` 最终 31 通过、0 失败、0 跳过。涵盖两项目独立身份、绑定缺失/冲突/撤销、成员及停用检查、视图与所有者篡改、业务底表及敏感列授权、角色继承、故障失败关闭。首次新代码测试 22 通过、2 失败：固定视图摘要使用了不同搜索路径，故障夹具替换函数遗漏参数名称；修正后一次 23 通过、1 失败，定位为视图规范化搜索路径不一致；统一固定搜索路径和重算版本控制迁移基线后复验通过。

同组包含真实浏览器联合流程：凭据登录、真实 D 策略、独立受限统计身份实际执行、独立审计身份读取关联 request_id、拒绝不支持的问题、后端重启后会话与审计恢复、退出、成员撤权及非成员拒绝。使用管理员在测试中撤销专用审计绑定来注入故障，浏览器收到 503 并显示带请求编号的审计错误；真实 PostgreSQL API 测试另验证故障时执行器未被调用。业务连接使用独立非超级用户测试身份且无审计底表和旧视图 SELECT；为运行测试对其他业务表授予较宽权限，不能证明生产最小权限。管理员仅负责隔离准备和故障注入；C 为 Mock。扩充浏览器故障场景后先后两次 11 通过、1 失败：失败原因是错误提示匹配到两个同样文案的 alert，改为使用响应 request_id 定位后，`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly` 最终 12 通过、0 失败/跳过。这与上面 31 项有重叠；查询中途撤权和其他真实故障场景仍不视作全部通过。

`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 63385 -WebPort 63384`：真实 PostgreSQL 业务/安全组 61 通过、PostgreSQL DataPort 契约 41 通过、浏览器 2 通过，0 失败/跳过；测试组有重叠。`python -m pytest backend/tests/test_scoped_audit_wiring.py backend/tests/test_audit_startup_preflight.py backend/tests/test_dev_composition.py backend/tests/test_data_binding.py backend/tests/test_secure_query_access_audit.py backend/tests/data_contracts backend/tests/security_contracts -q -rs`：77 通过、6 跳过，跳过项要求真实数据库 provider；该组以 Mock 和替身为主。Linux 只读工作区回归 `python -m pytest backend/tests -m 'not real_database and not legacy_persistence' -p no:cacheprovider -q -rs --tb=short`：506 通过、1 跳过 Windows 专属探针、181 未选入。测试数量不跨组相加。

Ruff 全部通过、后端 120 文件格式检查通过、OpenAPI 一致性通过，前端 `npm run typecheck` 和 `npm run build` 通过（大包警告仍在）。`docker build -f Dockerfile.integration -t paperpilot-phase67-test .` 因 Docker 默认 DNS 无法解析依赖源而失败；通过 `--add-host` 临时指定经本机 DNS 查询的依赖源地址后，最终代码的镜像构建成功，并确认候选迁移已打入镜像。该结果不代表默认网络环境、完整 Compose 部署、生产 TLS 或多用户生产安全已验收。无生产/未知数据库操作、提交或推送。

## Phase 6.7：多身份继续联调（2026-10-29，部分完成）

隔离测试组合入口支持以受保护环境注入的 JSON 列表配置至多 64 组用户、项目与独立数据库登录，保留原单组配置；两种形式混用、重复映射/登录、额外字段和数据库目标不一致均拒绝。没有凭据打印和日志输出。路由只按可信会话用户及服务端项目上下文查找映射；每次读取建立并关闭该身份专属连接，数据库视图再次校验绑定。所有映射在启动与全局健康检查时验证，安全查询仅检查对应项目；有问题的配置不会静默回退。普通业务用户读取查询改为只选择公开用户字段，不再把密码摘要带入 DataPort 用户映射；认证与业务仍共享数据库身份，因此并不证明该身份无法读取密码摘要或其他项目业务表。

本轮凭据轮换支持受控重启：在隔离库中变更专用测试账号密码后，旧配置连接被拒绝，使用新配置创建的读取器可访问原项目；取消绑定后该身份的新读取被拒绝，其他项目读取不受身份路由影响。配置固定于进程启动；没有集中秘密管理、在线热更新、自动密钥轮换或多 worker 原子切换。运维须验证新实例并排空旧实例后撤销旧凭据。运行中的查询仍可能持有旧快照；全局健康检查遇到任一配置失效会报告故障。默认 SQL 执行继续关闭，生产配置仍拒绝启动。

Windows / Python 3.12.6 / Node 24.14.1，独立 PostgreSQL 16.15：`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1` 32 通过、0 失败/跳过；代码最后调整后 `-ScopedOnly` 13 通过、0 失败/跳过。两组重叠，不能相加。真实隔离测试覆盖两个项目专用登录、服务端身份路由、跨项目拒绝、错误/重复映射、绑定撤销、凭据轮换及项目范围的就绪检查。真实浏览器在同一应用实例中以两组凭据分别读取对应项目审计，并验证真实 D 策略、受限 SQL、审计故障和撤权；C 为 Mock。管理员仅准备与故障注入，业务测试身份并非生产最小权限，统计查询身份和审计读取身份独立受限。初次并行运行中浏览器脚本先于测试夹具更新而收到 422，12 通过、1 失败；同步后重新运行通过。

`powershell -NoProfile -File scripts/test-phase65-isolated.ps1 -Browser`：隔离 PostgreSQL 凭据测试 6 通过、浏览器入口 1 通过；业务身份为测试管理员，C/D 为 Mock。`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1 -ApiPort 63485 -WebPort 63484`：真实数据库业务和策略组 61 通过、PostgreSQL 共享契约 41 通过、浏览器 2 通过；浏览器业务连接使用测试管理员，测试主动重启会产生代理断连日志。`python -m pytest backend/tests/test_scoped_audit_wiring.py backend/tests/test_audit_startup_preflight.py backend/tests/test_dev_composition.py backend/tests/test_data_binding.py backend/tests/test_secure_query_access_audit.py backend/tests/data_contracts backend/tests/security_contracts -q -rs`：78 通过、6 跳过，跳过项要求数据库 provider。Linux 联调容器非真实数据库回归：507 通过、1 跳过 Windows 专属探针、182 未选入。前端 `npm run build` 通过并包含类型检查，存在大包警告；OpenAPI 一致性与 Ruff/格式检查通过。

使用经本机 DNS 解析、`--add-host` 临时映射的方式构建 `Dockerfile.integration` 成功；新镜像在独立 Mock 容器中启动、健康检查 HTTP 200 并停止。生产环境配置检查按既有门槛拒绝启动（预期退出 1）。上述不是带真实 PostgreSQL、独立角色和 TLS 的正式容器部署验收；Docker 默认 DNS 的历史问题仍未处理。未操作生产或未知数据库，没有提交或推送。

## Phase 6.7：认证连接分离与多项目统计候选复验（2026-10-29，部分完成）

真实 SQL 默认关闭、生产模式拒绝启动；以下均仅在本机隔离测试环境有效。`PAPERPILOT_AUTH_DATABASE=isolated` 明确注册独立认证数据库连接，要求认证与业务登录不同、同一本机数据库，并在启动、健康检查和每个认证事务前检查角色与权限。认证连接单独关闭，故障不回退共享连接。检查认证角色不得为超级用户、BYPASSRLS、角色成员或能创建数据库/schema/临时对象，拒绝其业务表权限、可调用的非系统 SECURITY DEFINER、审计读取及多余会话表权限；业务连接不得读取或改写密码摘要、访问认证表或改写用户停用状态。检查不覆盖所有数据库函数和系统对象，因此不能证明完整生产最小权限。为避免竞态，认证实现仍使用行锁，候选认证身份需要 `users.password_hash` 的 UPDATE 权限，受攻陷后可能篡改密码摘要，构成正式部署阻塞项。业务测试身份对其他业务表权限较宽，不代表生产最小权限。代码不打印凭据，连接配置的 repr 隐藏密码；环境变量仍非正式密钥管理，缺少集中供应、动态轮换与撤销。

`PAPERPILOT_SECURITY_DB_BINDINGS` 显式配置最多 64 个项目与不同受限登录；以服务端项目上下文选择身份，每项目连接复用原候选的数据库 SESSION_USER 绑定、可信视图、授权与策略检查。缺失映射、重复项目/身份、与单项目配置混用及不满足安全前置条件均拒绝；单项目配置继续兼容。启动与全局健康检查要求所有配置就绪。连接逐次建立关闭，没有跨项目连接池复用；仍没有正式密钥生命周期和大量项目的运维方案。

最终本轮 `powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly`：隔离 PostgreSQL 16.15，16 通过、0 失败、0 跳过；其中浏览器测试使用正式密码会话、分别受限的业务/认证/两个项目统计/两个用户审计身份、真实 D 策略和执行器、Mock C。管理员只准备隔离角色、种子并注入故障；合法查询以受限身份执行。两个项目分别返回各自统计，审计/会话撤销及故障关闭、服务重启恢复均在真实浏览器场景覆盖；第二项目无论文，返回空统计。另一数据库测试通过两个项目的不同年份记录验证非空隔离、撤销绑定和拒绝访问，权限扩张和意外授权 SECURITY DEFINER 的预检拒绝由真实数据库验证。测试身份不构成正式环境审查。

`.venv\\Scripts\\python.exe -m pytest backend/tests/test_restricted_executor_candidate.py backend/tests/test_dev_composition.py backend/tests/test_phase5d4_runtime.py backend/tests/test_scoped_audit_wiring.py backend/tests/test_audit_startup_preflight.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：79 通过、0 失败、6 跳过；跳过项要求真实 DataPort provider，本组使用 Mock/替身为主。此前同轮独立 PostgreSQL DataPort 契约测试 41 通过；与其他分组可能重叠，不能相加。`npm --prefix frontend run test:e2e -- tests/auth-session.spec.ts tests/business-states.spec.ts tests/sprint-quality.spec.ts`：41 通过，使用浏览器请求替身和 Mock，不代表真实数据库验收。`npm --prefix frontend run build`、`npm --prefix frontend run typecheck`、Ruff、138 个 Python 文件格式检查和 `scripts/export_contracts.py --check` 通过；前端有现存大包警告。`git diff --check` 通过，Git 提示部分已有文件的 LF/CRLF 转换。

过程中的首次联合浏览器未就绪：审计预检闭包误引用新认证连接，修复为始终使用业务连接；随后故障注入早于前端完成健康读取而返回 403，调整测试等待就绪，并补充前端未知认证模式时先重新获取健康状态再进行写操作，相关恢复测试通过。扩大回归曾在 Windows 取得 133 通过、6 跳过、1 失败：既有符号链接测试因 WinError 1314 无创建权限失败，未删除或降低该断言。本轮未重新验证该用例的 Linux 环境。

正式部署仍阻塞：认证身份密码摘要写权限需经独立审查并收敛；业务身份其他表授权、身份与资源撤权、系统函数与所有者权限、密钥供应/轮换、TLS/代理与正式角色配置尚未完成生产验收；未使用生产或未知数据库，未提交或推送。开发配置和复现方法见开发指南。

本轮 `docker build -f Dockerfile.integration -t paperpilot-phase67-split-auth .` 成功，从锁文件安装依赖；以独立开发容器启动全 Mock 模式，健康检查返回 200、`a/c/d=mock`，随后停止本次创建的容器。镜像内调用生产设置校验明确拒绝启动（预期失败）。这是开发容器构建和 Mock 启动验证，不是带真实角色、TLS、秘密管理和外部依赖的干净生产部署验收。

## Phase 6.7：认证行锁与受保护文件继续整改（2026-10-29，部分完成）

本轮新增版本化的隔离候选 `backend/app/security/migrations/004_auth_lock_candidate.sql`；不由应用自动执行。认证会话创建使用经校验的 SECURITY DEFINER 函数对用户行 `FOR UPDATE`，认证登录不再具有 `public.users.password_hash` 的 UPDATE 权限；独立 NOLOGIN 函数所有者仅拥有必要列的读取和行锁所需更新授权，不能登录、继承或拥有成员。启动及认证事务前校验受版本控制的函数体、签名、搜索路径、所有者、EXECUTE 授权及所有者权限；业务角色不能调用该函数。此前关于认证登录必须直接拥有密码摘要更新权的描述仅适用于旧候选，已由本轮候选替代。开发指南记录显式隔离库准备步骤。尚未完成对生产管理员与 DDL 变更窗口、完整系统函数、正式业务角色及部署环境的独立审查。

隔离 PostgreSQL 16.15 的 `powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly` 最终 16 通过、0 失败、0 跳过（包含真实浏览器，C 为 Mock）。真实角色测试验证认证身份直接更新密码摘要被拒绝；不受信所有者、修改函数定义、额外 EXECUTE 授权或所有者越权均拒绝就绪；密码重置持有用户行锁时旧凭据创建会话实际等待，重置提交后被拒绝，旧会话被撤销，新密码可以登录。浏览器联合场景使用正式凭据会话、真实 D 策略及受限 SQL，不代表正式部署已经安全。另一次隔离 PostgreSQL 数据契约为 41 通过；`scripts/test-phase65-isolated.ps1 -Browser` 认证数据库测试 6 通过、浏览器入口 1 通过，组间可能重叠，不累加。

新增 POSIX 受保护文件输入，供业务数据库密码、认证密码及统计和审计单身份/多身份凭据清单使用；检查绝对路径、文件类型、所有者、group/other 权限、符号链接和大小，配置不安全即失败关闭。Windows ACL 尚不受支持，Windows 文件模式主动拒绝。Linux 联调容器以只读源码挂载运行 `python -m pytest /workspace/backend/tests/test_secret_config.py -p no:cacheprovider -q -rs --tb=short`：4 通过，包括文件权限和四类配置解析；这些是文件及解析测试，没有证明生产容器秘密挂载、集中密钥服务或在线轮换。重启和轮换说明见开发指南。

Windows 后端相关测试及 Mock 契约组：81 通过、0 失败、8 跳过，其中 2 项需要 POSIX、6 项需要真实 DataPort provider；对应 POSIX 文件测试和 PostgreSQL DataPort 已分别运行，测试组不能相加。Ruff、140 个 Python 文件格式检查、OpenAPI 一致性、前端类型检查和构建通过，前端仍有大包警告。本轮未重新运行 Windows 既有符号链接测试，先前 WinError 1314 限制仍存在。

`docker build -f Dockerfile.integration -t paperpilot-phase67-auth-lock .` 首次因 Docker DNS 无法解析依赖源失败；本机解析依赖源并以 `docker build --add-host files.pythonhosted.org:151.101.0.223 --add-host pypi.org:151.101.128.223 -f Dockerfile.integration -t paperpilot-phase67-auth-lock .` 重试成功。开发 Mock 容器健康检查通过，安装包包含候选迁移；该验证不覆盖真实 PostgreSQL 身份、TLS 和正式部署。真实 SQL 默认关闭，生产模式仍拒绝启动；没有提交、推送或操作未知数据库。

## Phase 6.7：Windows ACL、业务最小权限候选与容器联合验证（2026-10-29，部分完成）

新增 Windows 文件读取候选：使用文件句柄检查有效身份、文件所有者和受保护 DACL，仅允许当前用户的简单显式允许 ACE；要求本地固定磁盘上的普通文件，拒绝其他账户授权、继承、重解析点、未知 ACE、并发可写共享及异常大小。读取与权限检查使用同一打开的句柄。Windows 的 OS、管理员、备份/调试特权主体及同一运行身份下其他进程仍在信任边界；尚未经过独立 Windows 安全审查。实际 Windows 测试验证当前用户专属 ACL 可读、默认继承和 Everyone 授权被拒绝，并验证写入句柄冲突、大小限制及重读更新文件。Windows 上相关组 82 通过、8 跳过，其中 2 个 POSIX 测试另在 Linux 容器中运行；Linux 文件组 4 通过、1 跳过 Windows ACL 测试。

新增 `PAPERPILOT_BUSINESS_PRIVILEGE_PREFLIGHT=required` 隔离候选及受版本控制的表操作权限基线：业务/worker 身份只获得现有查询、项目、任务、报告、outbox 插入等实际所需表权限，拒绝报告历史删除、无关表/列授权、危险 DDL 权限及已列明的高风险函数；业务身份不读取密码摘要、会话表或审计底表。隔离数据库实际验证权限漂移导致健康检查 503；以该身份完成项目成员操作、论文任务、报告引用快照、审计和 outbox 写入。此配置仍按表级操作赋权，未完成列级最小化；业务角色可以跨项目直接读取所授权业务表，隔离仍依赖应用授权，不能声称数据库原生项目隔离。outbox 投递身份仍须独立审查，生产模式仍拒绝启动。

最终 `powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -ContainerImage paperpilot-phase67-boundary-test`：Windows / PostgreSQL 16.15，16 通过、0 失败、0 跳过；包含真实浏览器和显式本地 Docker 测试镜像。该组合使用真实数据库、分别受限的业务/认证/两组统计与审计身份、真实 D 策略和执行器，C 为 Mock。测试创建独立 PG 容器、运维角色准备测试对象，应用不使用管理员执行统计。单独应用容器真实执行统计查询并关联审计，容器重新创建后会话和审计仍可读取。该测试凭据通过进程环境注入，不证明生产秘密挂载或 TLS。

另在真实隔离 PostgreSQL 上使用 Windows 受保护临时文件供应认证数据库密码，排空旧进程后更改专用测试登录密码：旧凭据不能创建新数据库连接，更新受保护文件并启动新实例后正常登录。仅验证单实例重启轮换，不是动态热更新、多实例原子切换或长期密钥服务。测试脚本只停止本次创建的隔离容器；未操作生产或未知数据库。

最终 Python 相关测试及 Mock 契约：82 通过、0 失败、8 跳过（2 项 POSIX、6 项需真实数据库 provider）；Ruff、142 个 Python 文件格式、OpenAPI 一致性、前端类型检查和构建通过，存在已有大包警告。各测试组存在重叠，不相加。新镜像构建使用经本机 DNS 解析的临时 `--add-host`，默认 Docker DNS 和带 TLS/集中秘密的正式部署未验收；Windows 既有符号链接用例本轮未运行。没有提交或推送，真实 SQL 默认关闭。

本轮最后一次变更将业务授权基线检查从健康检查扩展到受控业务数据库的每个事务入口，包含后台 worker；检查失败回滚并返回不可用，权限恢复后可以重新处理。隔离 PostgreSQL 测试验证运行中授予历史报告 DELETE 权限后，健康检查与既有会话请求返回 503；撤销授权后恢复。`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -ContainerImage paperpilot-phase67-guard-test` 使用最终代码重新构建的本地测试镜像，16 通过、0 失败/跳过，覆盖真实测试身份 SQL、容器重建、审计及真实浏览器（C 为 Mock）。表级业务权限核验每个事务都会带来数据库目录查询开销，尚未进行性能验证；对数据库管理员同时变更权限或对象的事务内竞态仍需独立审查。

最终 Python 测试组合 `.venv\Scripts\python.exe -m pytest backend/tests/test_secret_config.py backend/tests/test_restricted_executor_candidate.py backend/tests/test_dev_composition.py backend/tests/test_phase5d4_runtime.py backend/tests/test_scoped_audit_wiring.py backend/tests/test_audit_startup_preflight.py backend/tests/test_database_pool_shutdown.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：85 通过、0 失败、8 跳过（2 项需 POSIX、6 项需真实数据库 provider）；最终 Ruff、142 文件格式、OpenAPI、前端类型检查和构建通过。上述结果取代本小节前面使用旧镜像和旧测试组合的数字，但不改变真实业务表行隔离、正式密钥服务、TLS 和独立安全审查仍未完成的结论。
# Phase 6.7：业务项目隔离候选增量验证（2026-10-29）

状态：部分完成，未进行正式部署或完整业务 API 的数据库原生隔离验收。环境为 Windows、Python 3.12.6、Node 24.14.1、npm 11.11.0；隔离容器为 PostgreSQL 16.15。候选迁移只在测试脚本新建的专用容器中执行，管理员只负责测试环境准备；实际项目读取、跨项目写入和撤权查询使用不同的专用受限测试角色。候选不注册到现有应用，真实 SQL 执行默认关闭。

| 本轮命令（仓库根目录） | 实际结果 |
| --- | --- |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls` | 20 通过、0 失败、0 跳过；包括新增 4 项业务项目隔离测试及既有审计/认证/统计隔离测试。 |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-phase6-security-browser.ps1` | 4 通过、0 失败、0 跳过；其中安全执行浏览器测试使用开发身份、真实 D 策略及受限 PostgreSQL，正式凭据浏览器测试使用 Mock D。该组没有验收上述业务 RLS 候选。 |
| `.venv\Scripts\python.exe -m pytest backend/tests/test_secret_config.py backend/tests/test_scoped_audit_wiring.py backend/tests/test_dev_composition.py -q -rs --tb=short` | 15 通过、0 失败、2 跳过；跳过项要求 POSIX 环境。 |
| `.venv\Scripts\python.exe -m pytest backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short` | 43 通过、0 失败、6 跳过；6 项需显式真实数据库 provider，当前为 Mock。 |
| `.venv\Scripts\python.exe -m ruff check backend scripts` | 通过。 |
| `.venv\Scripts\python.exe -m ruff format --check backend scripts` | 144 个文件通过。 |
| `.venv\Scripts\python.exe scripts/export_contracts.py --check` | OpenAPI 与应用 Schema 一致。 |
| `npm --prefix frontend run build` | 类型检查与构建通过；存在 JS 大包警告。 |
| `git diff --check` | 通过，提示部分既有文件换行格式转换。 |

受限角色使用 `SESSION_USER` 与唯一的用户/项目绑定访问 22 张开启强制 RLS 的表。测试覆盖两个项目的实际读取隔离、伪造项目上下文、跨项目写入拒绝、成员撤销后的后续读取拒绝、错误/缺失/重复绑定、列授权和 RLS 漂移失败关闭。该结果不代表全部项目表列级最小权限已完成，也不代表后台 worker、项目创建和全部业务读写已接入。测试之间存在重叠，不能将各组通过数相加视为总数。

本轮未验证生产角色及对象授权全貌、生产密钥保险库与多实例在线轮换、完整用户停用撤销、正式环境部署和新候选的浏览器端到端。项目绑定和 DDL 的运维可信边界、系统函数与所有列的最小授权仍需独立审查；不能在现有数据库直接应用该候选迁移。真实 SQL 继续默认关闭。

## Phase 6.7：业务读取身份路由增量（2026-10-29）

状态：部分完成。新增仅限 `environment=test` 的显式 `ScopedBusinessRouter` 注入，将论文、来源、任务和报告的指定 GET 路由按服务端用户和项目选择受限数据库身份；无绑定或预检失败时不回退。为防止未迁移接口使用共享身份绕过隔离，试验模式拒绝其他业务 API，且不启动后台 worker。它没有接入默认开发组合，亦不是完整 DataPort 或正式部署方案。

本轮 Windows、Python 3.12.6、PostgreSQL 16.15 结果如下（各组含重叠用例，不能相加）：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：6 通过、0 失败、0 跳过。项目读取使用真实受限 PostgreSQL 身份；新增 API 路由测试的认证和项目授权为测试替身，未证明正式认证与 RLS 的联合安全性。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：22 通过、0 失败、0 跳过。包含现有正式凭据、真实 D 策略、受限查询及隔离审计的浏览器联合场景；该浏览器场景不使用新业务 RLS 路由，AI 为 Mock，容器部署可选分支本次未启用。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_scoped_audit_wiring.py backend/tests/test_dev_composition.py backend/tests/test_task_recovery.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：68 通过、0 失败、6 跳过；跳过项需要显式真实数据库 provider。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（144 文件）和 `.venv\Scripts\python.exe scripts/export_contracts.py --check`：均通过。

未实现：项目创建、业务写入、全局任务领取及 worker 的数据库原生隔离，完整列级最小授权和生产密钥供应轮换；未完成新业务 RLS 与正式认证、真实安全查询、隔离审计的一体化浏览器验收。RLS 候选不能直接应用到既有数据库，真实 SQL 仍默认关闭。

## Phase 6.7：受限业务写入候选增量（2026-10-29）

结论为部分完成。仅在专用隔离 PostgreSQL 中应用 `005_candidate_business_rls.sql` 和 `006_candidate_business_actor_checks.sql`，后者为论文导入、任务、幂等及 outbox 写入增加操作者与受信数据库登录的绑定校验。`ScopedBusinessWriter` 使用受限身份和六张表的列级 INSERT 清单执行论文导入与任务创建，不注册到 API 或 worker；这不代表项目创建、报告保存、任务领取或其他写入已经完成数据库隔离。

隔离测试中，管理员只负责创建测试对象和授权，业务写入使用专用受限角色。验证了论文和任务同键重试、跨项目上下文拒绝、伪造 outbox 操作者拒绝、写入权限及策略漂移时拒绝就绪，以及通过提交前故障注入验证论文、幂等记录和 outbox 一同回滚。故障注入为测试替身，不是数据库断线测试。

本轮 Windows / Python 3.12.6 / PostgreSQL 16.15 实际结果：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：23 通过、0 失败、0 跳过；包括现有正式凭据、真实 D 策略、受限统计执行器与审计的浏览器场景，但该场景没有使用本轮新增业务写入候选，C 为 Mock。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_scoped_audit_wiring.py backend/tests/test_dev_composition.py backend/tests/test_task_recovery.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：68 通过、0 失败、6 跳过，跳过项需要显式真实数据库 provider。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（144 文件）及 `.venv\Scripts\python.exe scripts/export_contracts.py --check` 均通过。

测试组存在重叠，不相加。未完成项目创建及后台任务的受控身份与路由、报告和其余业务写入、生产凭据供应与轮换，以及正式认证、业务 RLS、安全查询和审计在同一应用中的浏览器联合验收；真实 SQL 继续默认关闭。

## Phase 6.7：报告写入和任务领取候选（2026-10-29）

本轮为部分完成：新增仅限隔离测试的 `007_candidate_report_actor_checks.sql`、`008_candidate_worker_actor_checks.sql`。`ScopedReportWriter` 限定报告作者及列权限，沿用现有报告版本链约束；`ScopedClaimWorker` 使用可信的用户/项目范围调用领取，默认 `PostgresData` 仍保持原全局领取行为。两种候选均未接入业务 API 或后台服务，不构成正式部署方案。

真实隔离 PostgreSQL 测试覆盖同项目跨成员编辑、历史版本及引用快照、并发版本冲突、模拟提交前失败回滚、伪造作者拒绝、并发领取、租约到期重新领取、其他成员任务未被领取和绑定撤销后拒绝领取。管理员只用于建立隔离测试环境及模拟租约过期，实际候选读写与领取使用受限身份。故障注入不是数据库断线测试；旧 worker 结果提交尚未由该候选支持或验证。

本轮 Windows / Python 3.12.6 / PostgreSQL 16.15 实际执行结果：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：25 通过、0 失败、0 跳过。包含现有正式凭据、真实 D 策略、受限统计执行器和审计读取的联合浏览器测试；该浏览器测试未使用本轮报告和 worker 候选，C 为 Mock。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-phase5a-isolated.ps1`：既有真实 PostgreSQL 业务测试 61 通过、共享 DataPort 契约测试 41 通过、Playwright 2 通过，均无失败和跳过。该脚本的业务与契约测试使用隔离数据库管理员身份，不证明数据库最小权限；浏览器使用开发身份，安全执行未启用。重启测试期间出现预期的连接重置日志。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_dev_composition.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：64 通过、0 失败、6 跳过；6 项需显式真实数据库 provider。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（144 文件）及 `.venv\Scripts\python.exe scripts/export_contracts.py --check` 均通过。测试组相互重叠，不应相加。

未完成：项目创建时的可信身份初始化、其他业务写入、worker 结果提交与失败处理、任务发起人撤权后的调度策略、全部候选的运行时及密钥生命周期接入，和正式认证、业务 RLS、安全查询、审计同一链路的浏览器联合验收。真实 SQL 仍默认关闭。

## Phase 6.7：任务结果与失败处理候选（2026-10-29）

部分完成。`ScopedFailureWorker` 和 `ScopedResultWorker` 仅供隔离库使用；新增 `009_candidate_worker_result_checks.sql` 通过调用者权限函数和限制性 RLS，在结果、证据、来源和片段写入时检查项目、用户及有效任务租约，预检验证函数和策略。候选未接入 API 或后台服务，且没有完成数据库状态机和生产凭据审查。

本轮 Windows、Python 3.12.6、专用隔离 PostgreSQL 16.15：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 10 通过、0 失败、0 跳过。使用受限身份验证进程类、证据、回答和草稿结果提交，失败处理、重复操作、旧尝试拒绝、直接数据库旧尝试写入拒绝、事务回滚以及函数和策略漂移拒绝；结果内容均为受控测试夹具，并非真实 AI 验收。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：26 通过、0 失败、0 跳过；在最后增加回答和草稿测试断言之前执行，包含原有正式认证、安全查询和审计联合浏览器测试，不包含新增 worker 候选的浏览器接入。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_dev_composition.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：64 通过、0 失败、6 跳过，需真实数据库 provider 的用例在此 Mock 测试组跳过。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（144 文件）和 `.venv\Scripts\python.exe scripts/export_contracts.py --check` 均通过。各组存在重叠，不能相加。

项目创建的可信身份初始化、其余业务写入、候选与正式认证及审计的运行时联合接入、生产密钥和安全审查仍未完成；受限凭据持有者直接更新项目内状态的风险尚未由数据库完全约束，不能启用为正式 worker。成员撤权后的未完成任务托管仍需设计。真实 SQL 继续默认关闭。

## Phase 6.7：项目创建身份候选（2026-10-29）

状态为部分完成。新增仅限隔离测试的 `010_candidate_project_bootstrap.sql` 和 `ScopedProjectCreator`：数据库根据专用登录的 `SESSION_USER` 确定用户，经独立 NOLOGIN 函数所有者在同一事务中写入项目、负责人及 outbox。测试包括不同用户伪造、调用方直接读取业务表、停用用户直接调用、绑定缺失、策略/函数/权限漂移与 outbox 故障回滚。项目创建者使用受限身份，管理员仅用于初始化及故障注入；故障注入不等同于真实断线测试。

本轮 Windows、Python 3.12.6、隔离 PostgreSQL 16.15 实际结果：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 12 通过、0 失败、0 跳过。首次试运行因多个临时数据库共用 PostgreSQL 集群角色，出现 11 通过、1 个测试初始化错误；已限定复用本次专用容器中的 NOLOGIN 角色，最终复验通过。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：28 通过、0 失败、0 跳过，包含既有正式认证、受限统计和审计联合浏览器场景；此组在最终增加公共绑定函数预检和篡改断言之前执行，候选最终代码由上面的隔离组复验。浏览器未接入本轮项目创建候选及业务 RLS 全链路，C 为 Mock。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_dev_composition.py backend/tests/test_task_recovery.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：64 通过、0 失败、6 跳过；跳过项需要显式真实数据库 provider。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（146 文件）、`.venv\Scripts\python.exe scripts/export_contracts.py --check` 和 `git diff --check` 均通过。各测试组重叠，不能相加。

当前项目创建候选尚未自动供应新项目的数据库身份，未与其他候选策略完整共存和正式认证路由联测；创建未知提交结果没有幂等重放机制，不能盲目重试。数据库状态转换、密钥管理、角色和函数的独立安全审查以及完整浏览器联合验收仍未完成；真实 SQL 保持默认关闭。

## Phase 6.7：项目创建对账及提交错误分类（2026-10-29）

状态：部分完成。新增仅供隔离测试的 `011_candidate_project_replay.sql` 和 `ScopedReplayProjectCreator.create_project_with_key`，采用独立操作键而非 request_id；原 `DataPort.create_project`、OpenAPI 和前端类型没有变化。原项目创建方法仍无幂等键。重复操作只在相同用户和相同参数下返回已创建项目，并发请求依靠数据库唯一约束；回滚时对账记录、项目和 outbox 均不保留。新候选未接入 HTTP 或正式部署。

`PostgresDatabase.transaction` 现将写事务 COMMIT 期间的非约束异常保守标记为 `DATA_COMMIT_UNKNOWN` 并丢弃连接，不因后续 rollback 成功而断定写入失败；约束冲突仍有确定语义。最终测试在真实隔离 PostgreSQL 上先提交，再通过连接替身模拟确认丢失并以原操作键在新适配器实例对账；未注入真实网络断线或真实进程崩溃。

本轮 Windows / Python 3.12.6 / 隔离 PostgreSQL 16.15：

- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 15 通过、0 失败、0 跳过，覆盖并发同键、跨用户、绑定切换和撤销、参数冲突、失败回滚后重试及模拟确认丢失。写入使用受限身份，管理员只用于隔离环境初始化和故障注入。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：30 通过、0 失败、0 跳过；执行于最后补充确认丢失和绑定切换测试之前。包含现有正式认证、真实 D 策略和审计浏览器测试，不包含新项目候选的浏览器接入，C 为 Mock。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-phase5a-isolated.ps1`：真实 PostgreSQL 业务测试 61 通过、DataPort 共享契约 41 通过、浏览器 2 通过，均无失败和跳过；使用隔离库管理员身份及开发身份，不能证明生产最小权限。重启测试产生连接中断日志。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_dev_composition.py backend/tests/test_task_recovery.py backend/tests/test_database_commit_outcome.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：69 通过、0 失败、6 跳过；需显式真实数据库 provider 的用例在此组跳过。
- `.venv\Scripts\python.exe -m ruff check backend scripts`、`.venv\Scripts\python.exe -m ruff format --check backend scripts`（147 文件）和 `.venv\Scripts\python.exe scripts/export_contracts.py --check` 均通过。不同组有重叠，不应相加。

尚未完成：项目身份与密钥的自动安全供应及轮换、将独立操作键以兼容方式接入现有业务和前端、其他业务写入和后台状态转换的完整数据库隔离、正式认证与业务 RLS/统计查询/审计在同一应用中的联合浏览器验收。真实 SQL 执行保持默认关闭。

## Phase 6.7：项目创建与业务 RLS 候选共存（2026-10-29）

状态：部分完成。业务预检仅接受 010 的三个已知项目创建策略：限定固定 NOLOGIN 角色、策略类型、表和表达式，并检查角色成员关系和数据库及 schema 创建权限。任何策略不完整、角色变更或表达式变更都会拒绝就绪。项目创建角色对 005 的 `bound_identity` 增加执行授权，以便 006 的 outbox 操作者检查执行；创建候选对该函数的受审查迁移定义、所有者、安全属性和授权进行预检。创建与业务读取候选仍分别校验，不能把业务读取预检作为整个项目创建安全边界的验证。

本轮隔离 PostgreSQL 16.15、Windows / Python 3.12：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终 17 通过、0 失败、0 跳过。测试显式组合 005–010，在已创建项目中用新建受限身份验证项目可见范围及绑定撤销；测试角色授权或策略被篡改时拒绝就绪。管理员只用于隔离容器的准备、授予及故障注入；业务读取和项目创建使用对应受限身份。初次完整策略联测暴露 006 outbox 策略缺少函数执行授权，修复后复验通过。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：33 通过、0 失败、0 跳过；包含既有正式认证、受限查询、审计读取浏览器测试及业务候选测试，但浏览器没有使用新项目创建和业务 RLS 全链路。此组运行后补充了一条 `bound_identity` 安全属性的断言，该断言在上面的 BusinessOnly 最终回归中验证。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_dev_composition.py backend/tests/test_task_recovery.py backend/tests/test_database_commit_outcome.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：69 通过、0 失败、6 跳过；跳过项要求显式真实数据库 provider，不能计作持久化成功。
- `.\.venv\Scripts\python.exe -m ruff check backend scripts`、`.\.venv\Scripts\python.exe -m ruff format --check backend scripts`（147 文件）及 `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` 通过。相关测试组有重叠，不能相加。
- `npm.cmd --prefix frontend run typecheck` 和 `npm.cmd --prefix frontend run build` 均通过；Vite 报告压缩后部分文件块超过 500 kB 的构建警告。`git diff --check` 通过，Git 报告部分现有文件的换行符提示。

创建后新项目身份由隔离测试管理员显式供应，未实现正式自动凭据供应、轮换和撤销；尚未将创建、所有业务写入及后台 worker 全部接入正式应用，也没有完成正式认证、业务 RLS、统计和审计的同一浏览器联合验收。真实 SQL 默认关闭，完整 Phase 6.7 及正式部署不能标记通过。

## Phase 6.7：受限上传 API 隔离试验（2026-10-29）

状态：部分完成。显式测试模式下，上传路由从服务端用户、项目选择 `ScopedBusinessWriter`，没有匹配写身份时拒绝，不走共享数据连接；健康结果标记上传试验。后台 worker 和其他未支持的写入仍关闭。上传原有公开 API、端口签名及 OpenAPI 未改。

Windows / Python 3.12.6 / 独立临时 PostgreSQL 16.15：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 18 通过、0 失败、0 跳过。新测试通过 HTTP 测试客户端和真实受限 PostgreSQL 业务写身份验证上传、同键重试、应用实例重建后的读取、跨项目缺少身份拒绝、绑定撤销与健康失败。登录使用开发会话，项目成员查询使用隔离测试管理员连接；审计由测试替身记录或注入故障，故障下无文件落地；不能作为正式认证及真实审计写入验收。初次测试因 Mock 审计夹具没有测试项目而失败，改为明确的审计替身后复验通过。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：34 通过、0 失败、0 跳过，含既有真实认证、受限查询、审计浏览器场景；该浏览器场景没有使用本次上传试验。最终补充健康状态断言后，由 BusinessOnly 复测。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：102 通过、0 失败、6 跳过；跳过项需要真实数据库 provider。首次误指定不存在的 `test_papers.py`，未执行测试；改用实际存在的 `test_api.py` 后通过。
- `.\.venv\Scripts\python.exe -m ruff check --no-cache backend scripts`、`.\.venv\Scripts\python.exe -m ruff format --check backend scripts`（147 文件）和 `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` 通过。各组存在重叠，不相加。

尚未接入正式模式、新项目身份供应、正式认证及审计的完整上传链路，也未接入后台 worker 或报告和其他业务写入。上传后任务在此试验中保持待处理，不代表 AI 已处理。真实 SQL 默认关闭。

## Phase 6.7：受限报告 API 隔离试验（2026-10-29）

状态：部分完成。测试模式显式注册 `ScopedReportWriter` 后，报告保存和引用来源校验通过相同用户、项目绑定的受限连接；缺失身份拒绝，不回退共享业务连接。报告版本与引用快照仍使用既有接口，健康接口标记试验。审计仍来自应用原有服务。

在 Windows / Python 3.12.6 的独立临时 PostgreSQL 16.15 中：

- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：35 通过、0 失败、0 跳过。报告 API 测试使用真实受限 PostgreSQL 业务身份和 HTTP 测试客户端，覆盖跨项目来源拒绝、历史版本和引用快照、同键重试，以及审计替身在提交前和提交后失败时的行为；成员查询仍使用隔离数据库管理员连接、登录为开发会话、审计为替身。组内另有既有真实认证、安全查询及审计浏览器测试，但未将本次报告试验纳入该浏览器链路。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：102 通过、0 失败、6 跳过，跳过项需要显式真实数据库 provider。
- `.\.venv\Scripts\python.exe -m ruff check --no-cache backend scripts`、`.\.venv\Scripts\python.exe -m ruff format --check backend scripts`（147 文件）及 `.\.venv\Scripts\python.exe scripts/export_contracts.py --check` 通过；不同测试组存在重叠。

数据库身份与密钥仍依赖隔离测试中的管理员手动供应。后台 worker 没有接入：005 的身份绑定函数未检查用户停用，账户停用后的后台处理及身份撤销尚未获得数据库保障；不得因为项目成员绑定仍在就推断用户可继续执行任务。正式认证、报告、后台任务与审计仍未完成同一浏览器联合测试。真实 SQL 默认关闭。

## Phase 6.7：停用账户的项目访问和任务提交隔离候选（2026-10-29）

状态：部分完成。`012_candidate_business_active_identity.sql` 在隔离候选中替换 005 的两个绑定函数，按数据库登录绑定及 `users.disabled` 过滤项目范围。业务与项目创建候选预检以 012 的受控函数体作为独立基线，要求函数所有者仅能读取用户标识与停用状态，禁止读取密码摘要及写入用户表。012 不在正式迁移链中，需显式在隔离库应用，不代表正式部署已迁移。

Windows / Python 3.12.6 / 新建隔离 PostgreSQL 16.15：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 20 通过、0 失败、0 跳过。使用专用受限身份验证账户停用后数据库项目行不可见，领取、结果提交及失败提交被拒绝，直接写入任务结果受 RLS 拒绝；恢复账户后新事务可再次通过预检。另验证函数定义回退及所有者获得密码摘要读取权会使预检拒绝。管理员仅用于测试环境准备与状态变更；这些不是生产数据库权限证明。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：36 通过、0 失败、0 跳过；含既有认证、审计和安全查询浏览器测试，浏览器并未接入本次业务 worker 候选。最后仅向 012 添加明确的 PUBLIC 执行权限撤销，随后以 BusinessOnly 复测最终迁移。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：102 通过、0 失败、6 跳过；真实数据库 provider 用例在该 Mock 组中跳过。
- Ruff、147 文件格式检查和 OpenAPI 一致性检查通过。组间有重叠，不相加。

该候选不撤销数据库登录口令，受限登录仍可能读取事先授权的非项目用户展示列；停用只在 READ COMMITTED 后续语句生效，不取消已运行语句或旧快照。没有验证真实密钥轮换、自动身份供应、独立后台 worker 进程接入或完整浏览器联合流程。需要继续完成受控供应与撤销、任务托管和独立审查；真实 SQL 保持默认关闭。

## Phase 6.7：受保护的业务身份清单及隔离组合（2026-10-29）

状态：部分完成。`scoped_business_config.py` 从受保护文件解析上限 64 项的服务端用户/项目与数据库身份映射，限定 `read`、`upload`、`report` 三个 profile，拒绝重复键、重复映射、非本机目标、配置不完整和跨模块数据库身份复用；清单不支持明文环境变量。`app.dev_composition:create_app` 仅在显式测试模式及持久化数据配置下接入，并在失败时关闭已初始化资源。此入口不会创建、轮换或撤销角色，也不会启用后台 worker。健康检查逐一验证映射，单一映射停用或故障会令整体就绪失败。

Windows / Python 3.12.6 / 隔离 PostgreSQL 16.15：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终 21 通过、0 失败、0 跳过。新增测试用 Windows 受保护 ACL 清单和真实受限数据库身份，通过组合入口验证两个项目、缺失映射、账户停用、显式修改测试角色密码后更新清单及应用重建；新密码可用、旧密码不能用于新启动连接。管理员只用于专用隔离环境准备和轮换。登录为开发会话，成员查询使用测试管理员连接；不是正式认证及完整数据库最小权限验收。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：37 通过、0 失败、0 跳过；包含既有真实认证、受限查询和审计浏览器场景，但没有将本轮清单及业务 worker 一起接入该浏览器场景。其后仅调整初始化失败资源关闭异常处理及对应单元测试，最终数据库候选由 BusinessOnly 复验。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_scoped_business_config.py backend/tests/test_dev_composition.py backend/tests/test_secret_config.py -q -rs --tb=short`：22 通过、0 失败、2 跳过。跳过为 POSIX 专属 owner/mode 测试，当前 Windows ACL 测试实际运行。
- Ruff、149 文件格式检查和 OpenAPI 一致性检查通过；测试组存在重叠，不能相加。

密码轮换测试先关闭应用，再更改隔离测试角色密码、更新清单并重建应用；没有证明在线轮换会撤销既存连接。正式身份自动供应、连接吊销、后台任务托管与独立 worker 接入、正式认证及业务 RLS/统计/审计同一浏览器联合测试仍未完成。真实 SQL 默认关闭。

## Phase 6.7：受限 worker 生命周期隔离试验（2026-10-29）

状态：部分完成。`scoped_worker.py` 将独立用户/项目的 `ScopedResultWorker` 桥接到既有任务编排；仅通过测试代码显式向 `create_app` 注入，要求对应业务身份、不同数据库登录、Mock AI 和显式审计回调。启动时检查所有 worker 后才开始处理，健康接口反映已知领取故障或异常退出，停服取消任务并关闭各 worker。未注册时受限业务模式仍停用 worker；默认组合和正式部署不启用该试验。

Windows / Python 3.12.6 / 明确新建的隔离 PostgreSQL 16.15：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终代码 22 通过、0 失败、0 跳过。通过 HTTP 测试客户端上传，由独立受限 worker 身份领取并提交结果，验证成功及受控 Mock 处理失败、账户停用后的健康失败；还验证缺少显式审计回调或重复 worker 被拒绝，以及启动预检失败时关闭资源且关闭异常不掩盖原始失败。Mock AI 返回合成片段；项目授权使用隔离测试管理员连接、认证为开发会话、审计为测试替身，不构成真实认证、模型或审计验收。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：38 通过、0 失败、0 跳过，包含既有正式认证、安全查询和审计浏览器场景；该浏览器场景未使用此次 worker。其后收紧启动顺序、资源关闭和身份校验并增加测试，最终业务候选由 BusinessOnly 再次验证。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/test_dev_composition.py backend/tests/test_scoped_business_config.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：121 通过、0 失败、6 跳过；跳过项需要显式真实数据库 provider。
- Ruff、150 文件格式检查与 OpenAPI 一致性检查通过。各测试组有重叠，不相加。

未完成：受限 worker 的真实 C 接入、真实审计回调与故障门槛、独立进程崩溃和任务托管恢复、身份自动供应与连接吊销，以及正式认证、业务 RLS、worker、真实安全查询与审计的同一浏览器联合测试。当前账户停用只阻断后续数据库操作，不取消在途外部调用；真实 SQL 默认关闭。

## Phase 6.7：worker 真实审计写入与 outbox 故障试验（2026-10-29）

状态：部分完成。worker 审计转发会先校验事件的用户与项目是否匹配该 worker 的服务端绑定。隔离测试为 worker、审计写入和 outbox 投递分别准备独立 PostgreSQL 身份；Mock AI 和开发会话仍用于本次任务，授权查询使用隔离测试管理员连接。

Windows / Python 3.12.6 / 新建隔离 PostgreSQL 16.15：

- `.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly`：最终 22 通过、0 失败、0 跳过。测试用独立受限审计身份写入真实审计事件并按 request_id、项目、用户关联，验证该身份不能直接 SELECT 审计底表；在任务处理期间撤销其 INSERT 权限，确认业务结果已提交、`process.completed` 审计未写入但 `complete_job` outbox 存在；恢复权限后使用独立项目绑定投递身份投递该事件，重复投递没有第二条审计。权限撤销为真实隔离数据库故障注入，不是数据库断网或进程重启。首轮恢复测试因投递角色缺少 `bound_identity` 所需 EXECUTE 权限而失败，补足该隔离角色授权后重跑通过。
- `.\scripts\test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：38 通过、0 失败、0 跳过，含既有认证、安全查询与审计浏览器测试；该组在最后增加真实 outbox 恢复验证之前运行，浏览器未使用本次 worker 链路。
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_scoped_worker.py backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：103 通过、0 失败、6 跳过，跳过项需要显式真实数据库 provider。
- Ruff、151 文件格式检查和 OpenAPI 一致性检查通过。各组存在重叠，不能相加。

风险：测试审计角色直接拥有 `audit_events` INSERT，现有表未实现数据库原生项目/操作者写入绑定；该角色可能通过直接 SQL 伪造其他有效身份的审计。应用层事件匹配检查不能取代数据库限制，故该身份不得用于正式部署。`process.completed` 写入失败后补投的 `complete_job` 是不同事件，未恢复原事件；投递进程重启、可靠对账及正式身份预检仍未验证。真实 C、正式认证与 worker/业务 RLS/安全查询/审计同一浏览器联合验收仍未完成，真实 SQL 默认关闭。

## Phase 6.7：worker 审计写入 RLS 候选（2026-10-29）

状态：部分完成。新增仅供隔离测试的 `013_candidate_worker_audit_rls.sql`，强制审计表 RLS，将项目事件写入与数据库登录绑定的用户和项目关联，指纹重放读取限定固定 NOLOGIN 函数所有者。该脚本不属于正式迁移链，缺少完整预检和系统级审计通道，不能部署到现有系统。

Windows / Python 3.12.6 / 隔离 PostgreSQL 16.15：`.\scripts\test-audit-candidate-isolated.ps1 -BusinessOnly` 最终 22 通过、0 失败、0 跳过。测试先在回滚事务中证实旧权限下受限审计角色可以插入其他项目/用户的伪造事件；显式应用 013 后，同项目事件写入和 outbox 恢复投递成功，跨项目、同项目冒充其他用户以及无项目事件的直接写入均被 RLS 拒绝。管理员只用于专用隔离环境准备、故障注入和验证；业务、worker、审计和投递各用独立测试身份。应用认证为开发会话、模型为 Mock，不证明正式认证、真实 C 或浏览器完整链路。

`.\.venv\Scripts\python.exe -m pytest backend/tests/test_scoped_worker.py backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：103 通过、0 失败、6 跳过；真实数据库 provider 用例在该组跳过。Ruff、151 文件格式及 OpenAPI 一致性检查通过。既有认证、安全查询和审计浏览器回归此前 38 通过，但没有使用 013 或本次 worker，不能计为候选联合验收。

013 目前没有独立的视图、策略、函数所有者与授权预检；固定角色需要可信供应及最小权限审查。它没有允许项目为空的登录/系统审计写入通道，不能与正式认证直接组合。还需验证角色成员关系、对象权限漂移、持久化投递器的重启恢复及真正的联合浏览器链路。真实 SQL 保持默认关闭。

## Phase 6.7：worker 审计身份预检候选（2026-10-29）

状态：部分完成。`ScopedWorkerAuditWriter` 在隔离候选审计写入前检查角色、授权、绑定、函数定义与 013 的 RLS 策略，支持显式将 `ready()` 用作测试应用启动及健康检查。012 和 006 的受版本控制内容提供函数预期；013 策略预期来自代码中的固定声明，而不是从被检查数据库即时采信。检查并非完整生产配置审查，且默认组合入口未接入此候选。

Windows / Python 3.12.6 / 新建隔离 PostgreSQL 16.15：

- `./scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：最终 22 通过、0 失败、0 跳过。使用独立受限审计角色写入：校验正常配置、修改 RLS 策略、授予审计底表列读取、变更指纹函数安全属性、打开角色继承、停用账户，以及恢复配置后的就绪与写入；策略变更时健康接口返回 503，显式审计就绪检查失败时应用启动被拒绝。SQL 身份和准备方式同上一节；不是正式认证或真实 C 联调。
- `./scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：38 通过、0 失败、0 跳过，包含既有认证、安全查询和审计浏览器场景；该组开始后又在业务测试增加了角色继承及账户停用检查，最终版本由上述 BusinessOnly 重新验证。浏览器场景没有使用此次 worker 审计候选。
- `./.venv/Scripts/python.exe -m pytest backend/tests/test_scoped_worker.py backend/tests/test_task_recovery.py backend/tests/test_api.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：103 通过、0 失败、6 跳过；跳过的共享用例未显式配置真实数据库 provider。测试组存在重叠，不相加。
- 全 `backend` 和 `scripts` 的 Ruff 检查、152 个 Python 文件格式检查及 OpenAPI 一致性检查通过。执行期间两次 BusinessOnly 初次运行因新增序列权限检查对非序列调用 PostgreSQL 权限函数而失败（每次 21 通过、1 失败）；修正后整组重跑通过。

剩余限制：013 只允许项目绑定审计，不提供匿名登录和系统事件通道；当前不保证数据库所有者、全部内置函数、并发 DDL 及凭据管理已满足生产安全需求。受限审计身份仍需可信供应，后台投递进程重启与原始事件补录未验证。正式认证、业务 RLS、真实安全执行和本候选 worker 审计尚无同一浏览器联合验收。真实 SQL 默认关闭，不能据此报告 Phase 6.7 完整通过。

## Phase 6.7：认证完成审计 RLS 候选（2026-10-29）

状态：部分完成。隔离候选 014 使用认证专用数据库登录，将已完成的会话事件与会话创建或撤销放在同一数据库事务。新增的限制性策略与 013 的公共策略叠加，防止该身份误绑定项目时扩大为项目事件写入；worker 的候选预检检查两条策略的准确表达式、受限身份与限制性属性。014 不自动迁移、不接入默认组合，不能部署到正式数据库。

Windows / Python 3.12.6 / 每次新建的隔离 PostgreSQL 16.15：

- `./scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：23 通过、0 失败、0 跳过。专用受限认证数据库身份通过 `PostgresSessions` 创建会话及事务内写入真实完成审计；拒绝项目事件和不支持的系统事件；向该测试身份错误绑定项目后仍拒绝项目事件。撤销审计 INSERT 后，登录创建及注销失败且事务回滚，原会话保持有效，授权恢复后注销成功。由测试管理员准备角色及注入故障，实际会话写入使用独立认证身份；测试使用直接调用和适配器替身，未通过 014 运行完整 HTTP 登录流程。现有受限 worker 预检验证两条 014 策略正常时接受、任一策略被改宽时拒绝。
- `./scripts/test-phase65-isolated.ps1 -Browser`：隔离 PostgreSQL 认证测试 6 通过、0 失败、0 跳过；独立真实浏览器认证测试 1 通过、0 失败、0 跳过。该测试没有启用 013/014，不是本轮候选联合浏览器验收。
- `./.venv/Scripts/python.exe -m pytest backend/tests/test_scoped_worker.py backend/tests/test_authentication_http_postgres.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：44 通过、0 失败、10 跳过，其中 4 项认证测试与 6 项持久化契约因该运行未配置隔离 provider 而跳过；上面的独立 PostgreSQL 认证测试实际运行，不将重叠测试数量相加。
- Ruff、152 个 Python 文件格式检查及 OpenAPI 一致性检查通过。最后增加断言后首次格式检查发现 1 个文件需格式化，格式化后复检通过。FastAPI 测试客户端发出依赖弃用警告。

014 仅处理已完成的会话事件，现有登录前的审计门槛、匿名拒绝等仍无隔离系统审计通道；数据库认证身份可以构造其允许范围内的事件，尚无完整防伪保证。尚需建立认证身份独立预检、正式角色与密钥供应、系统事件读取权限、受限审计路由，并在同一真实浏览器流程中验证认证、业务 RLS、安全执行和审计。真实 SQL 仍默认关闭；不能报告 Phase 6.7 完整通过。

## Phase 6.7：系统事件审计与认证侧预检候选（2026-10-29）

状态：部分完成。新增 015、隔离系统审计写入身份及认证侧附加预检候选。015 将登录前门槛和匿名安全查询拒绝限定为特定的无项目事件，另设限制性策略防止身份误绑定项目后借其他宽松策略写入项目事件。测试模式可显式注入系统审计，启动、健康和每次写入检查失败时拒绝；默认组合未接入。014 完成审计与会话变更仍在同一事务，登录前 `pending` 审计使用独立事务。

Windows / Python 3.12.6 / 新建隔离 PostgreSQL 16.15：

- `./scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`：最终 23 通过、0 失败、0 跳过。专用认证和系统审计身份分别在真实数据库写入会话完成事件、登录失败和匿名拒绝事件；通过 HTTP 测试客户端验证审计 INSERT 权限被撤销后登录失败、不返回会话，恢复后登录与注销成功。测试覆盖系统角色误绑定项目、审计策略和函数属性被修改、认证角色误绑定及认证策略被改宽时的拒绝，以及 worker 与 013/014/015 策略共存。角色、迁移和故障由隔离管理员准备；HTTP 项目授权数据仍通过测试管理员读取，认证数据库连接使用受限身份并由测试显式注入，不是正式完整认证边界验收。
- `./scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -BusinessRls`：39 通过、0 失败、0 跳过，包括既有真实浏览器认证/安全查询/审计场景。该组在最后新增认证侧附加预检之前启动，其浏览器场景未使用 015；最终候选由上述 BusinessOnly 重新验证，不能作为同一浏览器联合验收。
- `./.venv/Scripts/python.exe -m pytest backend/tests/test_api.py backend/tests/test_secure_query_access_audit.py backend/tests/test_scoped_worker.py backend/tests/data_contracts backend/tests/security_contracts -q -rs --tb=short`：94 通过、0 失败、6 跳过，跳过的真实数据库共享契约未在该组配置 provider。测试组有重叠，不合计。
- Ruff、154 个 Python 文件格式检查和 OpenAPI 一致性检查通过；测试客户端发出依赖弃用警告。

限制：候选迁移尚未加入正式迁移链；角色与随机口令由测试夹具在临时容器中创建，不是生产密钥供应或轮换。`CandidateAuthAuditGuard` 是附加预检，尚未与 `AuthenticationBoundary` 完整组合，不能证明正式认证角色全部最小权限。系统审计白名单不能防止该专用身份伪造其允许范围内的事件，系统事件读取与权限、所有者完整权限、投递恢复及独立安全审查仍未完成。实际浏览器尚未同时使用 015、受限业务身份、真实 SQL 执行和审计；真实 SQL 默认关闭。Phase 6.7 不满足完整验收条件。
