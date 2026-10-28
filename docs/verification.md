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
