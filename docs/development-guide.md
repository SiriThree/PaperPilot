# A/B/C/D 开发及交接指南

## 默认演示数据（显式选择，不接入正式数据层）

使用 `scripts/start-backend.ps1 -Demo` 或 `uvicorn app.demo:create_demo_app --factory --app-dir backend` 启动扩充样例；具体双端启动及演示顺序见 [README 的演示流程](../README.md#mock-演示流程)。默认 `app.main:create_app`、A/C/D工厂及共享契约测试不变，不需要环境变量开关或新依赖。`-Port` 可选择独立本机预览端口，默认8000。

数据定义和初始化位于 `backend/app/demo.py`。独立 Mock 内存项目保留 Alice/Bob 成员关系及 Eve 隔离；包含6篇虚构文献（5成功、1失败）、10个合成片段、2份证据、两版综述和Mock查询审计。除固定项目/成员fixture外，任务、处理结果、证据、报告版本均沿用DataPort，查询审计经过ResearchService和MockSecurity；没有为B增加真实数据库模型、迁移、解析算法或SQL执行。`FileReference`是Mock占位引用，不在磁盘伪造已上传PDF。

`create_demo_app`只接受development/test与全Mock配置，在worker启动前加载数据；失败即拒绝启动，没有真实模式回退。单应用重复进入生命周期不重复灌入，启动新进程重新创建初始样例。这是Mock重新初始化，不能作为真实持久化恢复证明。启动日志只输出演示标记、项目ID和失败task_id，后者可通过现有认证任务GET读取/明确重试。

前端新增`api.evidenceHistory`封装原有GET证据接口，研究页“查看已有证据”显示最新一份已保存结果，空结果和故障有明确反馈，只读操作不会触发AI。原提取、任务恢复、报告保存、引用及版本流程保留；没有新增HTTP接口、修改Schema/生成类型或AB-1。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_demo_data.py -q
npm.cmd --prefix frontend run test:e2e -- --config=playwright.demo.config.ts
```

独立演示浏览器测试在`frontend/demo-tests/`，配置使用8101/5274及临时存储，输出在忽略目录`output/demo-data/`。默认`frontend/tests/`的68项仍以最小fixture执行。两组配置均纳入类型检查，验证记录见 [verification.md](verification.md)。真实A/C/D实现不导入这套演示初始化逻辑。

## Phase 4B：页面状态、错误和业务联调

本轮沿用 Phase 3 的端口和装配方式，不改公开 Schema/OpenAPI、生成类型、依赖锁文件或数据库选型。PRD 第9.3节中已具备的服务端授权、任务编排、报告版本和网关边界直接复用；实现矩阵、A–H验收证据及限制见 [验证记录](verification.md)。

统一客户端 `frontend/src/api/client.ts` 经同源 Cookie 和原 CSRF 头调用既有 API。普通请求15秒超时，任务查询保留10秒上限并组合页面取消信号；不自动重试。网络断开为 NETWORK_ERROR，超时为 REQUEST_TIMEOUT，HTTP业务拒绝保留安全 code/status。请求编号优先使用合法的响应体 UUID，缺失或无效时使用 X-Request-ID。`api/errors.ts` 统一公开提示，未知错误、HTTP代理正文、网关reason和任务失败不直接展示供应商内部消息。C/D仍须遵守安全 AppError 约定，不能将密钥或原始内部 SQL 写入响应。

四页分别展示读取中的状态、成功数据、明确空结果和失败提示。`useResource` 只编排读取，不重放写入；失败清空该区域旧结果并显示对应重查入口，卸载或后续查询使迟到结果失效。项目列表采用同样的身份/请求范围保护。首次连接失败可“重新连接服务”；401清理当前身份与项目视图，返回开发登录并保留可用请求编号。退出失败仍保留当前界面并显示错误，不声称已经退出。

负责人可从文献表“移出项目”，确认“不再参与检索、历史报告保留快照”后调用原 DELETE API（confirm_source_impact=true）。取消不发送请求；成员没有按钮，但这不是授权保证，服务端仍拒绝非负责人。移出是项目关联操作，不负责删除原文件。

报告保存成功后，历史版本和报告列表独立刷新；刷新失败仅重读，不自动再次保存或创建版本。AI检索不可用时仍能手工编辑、保存和读取报告；查询结果与审计列表也分别展示，审计刷新失败不篡改已有查询状态。异步任务继续沿用 Phase 2C 的恢复记录、60秒状态未知、同任务查询和原键核对机制，恢复存储字段、任务编号与幂等规则不变；本轮补充上传前文件校验，未重设计恢复协议。

模式展示区分默认Mock、已注册适配器和未知模式，具体结果保留 mock/synthetic 标记。注册不能证明持久化、真实AI质量或SQL防护；页面不再只凭 persistent 模式承诺重启保留。

```powershell
# B 的实际故障隔离/权限集成测试（使用端口替身，不是正式模块验收）
.\.venv\Scripts\python.exe -m pytest backend/tests/test_business_integration.py -q
# 已安装依赖时，按下文统一命令跑全套；也可仅运行新页面用例
$env:PAPERPILOT_E2E_API_PORT='8100'
$env:PAPERPILOT_E2E_WEB_PORT='5273'
$env:PAPERPILOT_E2E_DATA_MODE='mock'
npm.cmd --prefix frontend run test:e2e -- business-states.spec.ts
```

启动及构建方式不变；浏览器用例自行启动隔离Mock服务，不能对日常运行库执行移出/成员变更测试。同步项目创建、成员变更和移出没有新增服务端幂等承诺，连接确认丢失时先重查/核对。报告保存的跨刷新未知提交、正式身份认证、真实数据库恢复与容器部署仍需独立验收；Docker未启动时只能检查 compose 配置，不能据此声称容器构建/启动通过。

## Phase 4B：本轮优化与复验

四页继续沿用原路由/API与Element Plus。项目名称支持就近校验和Enter提交；PDF在完整读取摘要前检查空文件、20 MiB和头部（不等于PDF解析/安全扫描）；年份倒置不发送查询。研究编辑器在草稿生成期间保留新编辑，结果到达后允许明确载入，显示字符/引用数与未保存状态。查询区分策略通过和实际执行，清除仅影响当前结果展示。

客户端只消费JSON业务端点，超时包括读取响应正文；健康元数据不完整、非JSON或HTTP 200错误信封均明确失败。未来文件/流式响应需要独立客户端边界，不能直接接入此JSON传输。服务端安全AppError仍由模块提供，不应携带内部SQL、凭据或资源秘密。

上传重放或已知回滚后，若本请求未采纳文件删除失败，仅记录`upload_cleanup_deferred`和request_id，保留已知业务结果。未知提交保留文件，禁止按超时自动删除。孤立文件对账/清理由A/B在真实存储接入时确认，本轮没有后台清理器。

来源校验的索引只存活于一次方法调用，检索论文元数据也只在单次请求复用；不跨请求缓存授权。A仍在资源访问和最终写入执行权限及事务约束；C/D不需改接口或前端。

本轮新增测试：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_sprint_reliability.py -q
# 沿用上节8100/5273和mock环境变量
npm.cmd --prefix frontend run test:e2e -- tests/sprint-quality.spec.ts
```

浏览器测试覆盖390/768/1440px四页和键盘跳转，截图保存在忽略目录`output/sprint-20261026/after/`。优化前后截图使用隔离演示数据，不能当作真实AI、持久化或SQL安全证据。逐检查点、实际命令和未验证项见[验证记录](verification.md)。Docker引擎可用后仍需补容器构建与启动；页面内未保存正文不具备跨刷新恢复。

## Phase 3：统一开工入口

本阶段只补齐独立适配器开发、注册和验收入口。AB-1（26 方法）、AIPort（5 方法）、SecurityPort（2 方法）、公开 Schema 和前端生成类型不变；没有新增数据库、算法或 SQL 网关实现。

| 成员 | 完整交接与职责 | 实现接口 | 业务装配参数 | 独立共享测试目录 |
|---|---|---|---|---|
| A | [成员 A](member-a-handoff.md)、[AB-1](ab-data-contract.md) | `adapters/ports.py:DataPort` | `data_binding=DataBinding(...)` | `backend/tests/data_contracts/` |
| C | [成员 C](member-c-handoff.md) | `adapters/ports.py:AIPort` | `ai_factory=build_ai` | `backend/tests/ai_contracts/` |
| D | [成员 D](member-d-handoff.md) | `adapters/ports.py:SecurityPort` | `security_factory=build_security` | `backend/tests/security_contracts/` |

入口都相对于 `backend/app/`。A/C/D 可以分别新增 `app/data/`、`app/ai/`、`app/security/`，这些是建议位置，当前并不存在真实模块。每人只实现自己的端口和隔离 fixture，不必修改 B 的业务服务或前端。已有 `app/persistence/` 是保留的 SQLite 历史实验，不代表数据库选型。

### 快速启动与现有契约

从仓库根目录执行 [README](../README.md) 的依赖安装和双终端启动命令，也可运行 `scripts/start-backend.ps1`、`scripts/start-frontend.ps1`。默认三个 Mock、开发身份；后端8000、前端5173。环境只读取进程变量，不自动加载 `.env`。已有服务需保留时，另选8100/5273：

```powershell
# 终端一，当前默认 Mock；注册真实实现后改为自己的组合入口
$env:PAPERPILOT_ALLOWED_ORIGINS='http://127.0.0.1:5273'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8100 --no-access-log
# 终端二
$env:PAPERPILOT_PROXY_TARGET='http://127.0.0.1:8100'
npm.cmd --prefix frontend run dev -- --port 5273
```

HTTP 契约为 `contracts/openapi.json`（Schema 在 components.schemas），源定义为 `backend/app/schemas.py`；正常/异常 HTTP 示例在 `contracts/examples/`，特殊结果在 `contracts/scenarios/`。内部端口定义在 `adapters/ports.py`，A 的逐方法示例在 `contracts/data-port-examples.json`，C/D 的全部方法、错误及审计实执行示例在 `contracts/module-port-examples.json`。二者属于内部端口示例，不是新增 HTTP API。

```powershell
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
```

A 的现有示例导出器 `scripts/export_data_examples.py` 是写入式生成命令，没有 --check 参数；日常只读校验运行现有契约测试，不要把重新生成误作检查。

### 显式注册，按模块独立替换

`app.main.create_app` → `adapters.factory.create_adapters` 是唯一业务组合入口。A 的 DataBinding 含 data/ready/可选close；C/D 的工厂各接收同一个 DataPort，分别返回 AIPort/SecurityPort。配置通过闭包传入，不让 HTTP 请求指定适配器模块或数据库连接。

```python
# 组合方式示意：binding/build_ai/build_security 由对应成员交付，非现成真实实现。
from app.config import Settings
from app.main import create_app as create_business_app

def create_app():
    return create_business_app(
        Settings.from_env(),
        data_binding=binding,
        ai_factory=build_ai,
        security_factory=build_security,
    )
```

此示例需设置 A=persistent/real、C=real、D=real，并提供全部对象才能运行。独立开发只传自己的参数并设置该模块真实模式，其余保持 Mock。成员交付例如 `app.member_c_app:create_app` 后，使用同样 uvicorn 命令替换模块路径；该入口尚需成员创建。模式变量本身不会加载实现：未注册、端口缺方法、构造/数据 readiness 失败均明确拒绝，禁止回退；mock 模式也拒绝额外真实工厂，避免被忽略。

C/D 工厂只做形状校验，不能证明算法效果或安全有效。资源构造失败时的清理由可信组合入口负责；A 正常退出通过 DataBinding.close，C/D 的 SDK/执行器生命周期由成员与 B 协调，当前端口没有新增 ready/close。生产配置始终拒绝，开发身份尚未替换；health 中 registered_*_unverified 也不是正式模块认证。

### 独立测试与前端联调

```powershell
# 默认 Mock：A 35项通过/6项真实库门槛跳过，C 10项、D 8项结构行为测试
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts backend/tests/ai_contracts backend/tests/security_contracts -q -rs
# 成员完成自己的 context manager provider 后，替换为实际模块名
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider your_package:build_harness -q -rs
.\.venv\Scripts\python.exe -m pytest backend/tests/ai_contracts --ai-contract-provider your_package:build_harness -q -rs
.\.venv\Scripts\python.exe -m pytest backend/tests/security_contracts --security-contract-provider your_package:build_harness -q -rs

.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m ruff check backend scripts
.\.venv\Scripts\python.exe -m ruff format --check backend scripts
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
# 浏览器测试自行创建隔离 Mock 服务，不连接已启动的真实成员环境
$env:PAPERPILOT_E2E_API_PORT='8100'
$env:PAPERPILOT_E2E_WEB_PORT='5273'
$env:PAPERPILOT_E2E_DATA_MODE='mock'
npm.cmd --prefix frontend run test:e2e
```

provider 都是 `build_harness(*, tmp_path)` context manager，分别返回 `data_contracts.harness.DataHarness`、`ai_contracts.harness.AIHarness`、`security_contracts.harness.SecurityHarness`；字段和故障配置见成员文档及实际 dataclass。只用临时库/文件、可控依赖和测试身份，不读取运行库或收费模型凭据。C/D provider 必须声明 is_mock=False；测试通过仍只代表所列行为，不替代真实检索评测、SQL 攻防或 A 的持久化证明。

联调由成员提供组合入口启动后，前端仍通过 `/api` 代理访问相同公开 API。先验证自己的共享契约，再和 B 跑该模块 API；最后由 B 组合三模块跑研究全流程。当前独立开发可开始；真实全链路前须对齐 B/C 文件 reader、A/C 正文/向量与索引提交、A/D 受限身份/审计、C/D 可选 NL2SQL 私有接口、正式认证与资源关闭，具体列在各交接文档末尾。

Phase 2C 的 [任务恢复说明](task-recovery.md) 定义当前前端恢复流程、最小浏览器记录和未知提交风险。B 不把页面恢复成功当作真实数据库恢复验收，正式幂等保留期、恢复和对账仍待 A 确认。

> 当前持久化接入以 [数据库中立说明](persistence-integration.md) 为准。数据库引擎由 A/团队决定；persistent/real 没有显式 DataBinding 即拒绝启动，B 不自动建库或迁移。下文 Phase 2B SQLite 描述保留为历史实验状态。

## 先固定交接面

参考 `contracts/openapi.json`、`backend/app/schemas.py` 和 `backend/app/adapters/ports.py`。每个接口的成功/异常示例在 `contracts/examples`，特殊网关和证据不足结果在 `contracts/scenarios`。

成员可以独立实现各自端口，在有可运行实现之前保持 Mock。当前模式为各适配器显式配置，任何真实适配器未实现都拒绝启动。不能只把工厂的 real 分支改为 Mock。

建议分工文件所有权：A 创建自身数据实现及迁移；C 创建 AI 实现和评测；D 创建安全实现和策略；B 维护业务路由、前端、配置、契约导出和集成测试。修改公共 Schema 先与调用方对齐，再更新文档、契约和样例。

## A 立即可以实现的接口

Phase 2A 已补充 [完整 A/B 契约 AB-1](ab-data-contract.md)：26 个方法的逐项输入/返回/权限/事务/冲突/重试规则、可执行样例和共享测试均以该文档为准。简短转交消息见 [成员 A 交接摘要](member-a-handoff.md)。

实现 `DataPort`，无需把 ORM 实体交给 B。所有输入输出使用公共 Schema，内部仅通过 Context 接收可信用户、项目和请求编号。

1. user_by_name/user_by_id/role：统一用户记录和项目成员关系；用户表及密码摘要只由 A 维护。
2. projects/create_project/members/add_member/remove_member：创建项目与负责人关系应同一事务；成员关系唯一。
3. import_paper/papers/paper/remove_paper/sources/evidence：项目内摘要唯一，论文与项目关联及任务创建同一事务；移出保留报告快照。
4. create_task/task/retry_task/claim_job/complete_job/fail_job：状态更新满足条件约束，领取具有并发互斥和租约恢复；最终结果与状态一致提交。
5. save_report/versions/reports：版本号唯一，保存事务包含引用快照；base_version_id 实现乐观并发控制。
6. controlled_statistics：仅允许约定意图，返回当前项目范围的结构化数据。这个接口不能成为 B 绕过 D 执行模型 SQL 的入口。
7. append_audit/audit：追加写与项目负责人只读查询；普通业务数据库角色不能修改或删除事件。

幂等作用域为用户+项目+操作+Idempotency-Key；同键不同载荷 409，重复同载荷返回原业务实体。真实实现需要数据库约束，不可仅用应用内字典保证。

失败时不要提交半完成事务，不要返回内部 SQL/连接异常。非成员和跨项目子资源统一 RESOURCE_NOT_FOUND，不泄露资源是否存在。

同次 Job 快照完成可幂等重放，迟到 attempts 不可覆盖新尝试。明确回滚映射 DATA_UNAVAILABLE；提交确认丢失映射 DATA_COMMIT_UNKNOWN，B 保留文件并使用原键对账。领域审计/outbox 与真实业务事务一起提交；B 的 requested/finished 日志不是事务保证。

共享测试默认只运行 Mock：`python -m pytest backend/tests/data_contracts -q -rs`。A 提供隔离库 provider 后使用 `--data-contract-provider module:build_harness` 复用同一测试；6 个真实数据库门槛要求重连、并发约束及租约恢复，不能用 Mock 代替。

用户认证逻辑由 B 协调。当前登录没有真实凭据；下一阶段 B/A/D 需共同约定密码摘要验证和持久化会话接口，不能创建另一套用户表。`DevelopmentSessions` 必须被替换，不可原样生产部署。

## C 立即可以实现的接口

实现 `AIPort`，保持供应商无关，不把模型 SDK 类型作为公共响应。每个方法接收 Context，调用时携带 request_id。

- process：接收 B 创建的 task_id 和 FileReference。B 保存原文件；C 通过受控存储访问。返回 ProcessResult 后由 B/A 原子提交片段与 searchable 状态。真实向量字段和批量提交结构先与 A 对齐；当前 Source 只是最小来源视图，并非完整向量记录模型。
- search：接收 SearchRequest 的模式、年份和数量；在数据访问阶段限定当前项目。不得全局召回后交前端过滤。额外检索元数据应先加入公共 Schema。
- answer：只接收 B 提供的当前检索片段，返回可对应的 citation_ids/sources 和证据不足标记；模型输出任意新来源会被 B 拒绝。
- extract：五类证据字段必须保留支持来源，未知内容用 not_mentioned/insufficient_evidence。
- draft：返回正文和引用清单，不写入报告版本。

每个方法交付正常、空结果/不足、超时及错误响应；不持有任意数据库执行凭据。当前 Mock 合成数据不可用于 Recall@5、引用支持率或真实 PDF 效果评测。

## D 立即可以实现的接口

实现 `SecurityPort.authorize/execute`。B 已实现每次请求的身份、成员和负责人基础检查，但这不是 D 的完整授权策略或数据库第二层约束。

- authorize：根据可信 Context、操作和角色实施最终策略，失败使用统一错误；不能把 role 或 project_id 当客户端可信声明。
- execute：只接收自然语言问题与可信上下文。若内部调用 C 生成 SQL，生成结果只进入 D 的 AST 解析和执行链路，不能返回给 B 直接执行。
- 区分 allowed/not_executed、allowed/executed、rejected/not_executed、undetermined/failed。只有 executed 可以携带查询结果。
- 关键审计持久化失败、策略无法判断、授权缺失、超时或网关不可用均拒绝执行/返回结果。明确 D 内部关键审计与 B 外部业务审计的关联责任，避免漏记或重复。
- 与 A 联合配置受限角色、受控视图、适用的行级安全和审计保护，并验证数据库身份不能绕过限制。

40 个攻击和 20 个正常用例由 D 在隔离、可重置真实测试库执行；当前测试只证明 Mock 调用边界及服务端应用层判断，不能代替 SQL 攻击实验。

## B 联调顺序

1. 与 A 打通用户/项目及持久化论文导入、任务、列表；验证服务重启后数据保留。
2. 与 C 打通文件访问、真实解析及有来源检索，再接问答/证据/草稿。
3. 与 D/A 打通授权、动态查询唯一执行链路和数据库受限身份；未验证前不开放真实统计执行。
4. 跑完整“上传→检索→问答→来源→报告版本→查询→审计”，再做干净环境交付。

业务服务通过端口注入；A 通过独立 DataBinding，C/D 通过各自显式工厂注册。开发可逐一替换模块；没有成员实现时不把模式名当作已接入。生产配置始终拒绝，完整生产能力尚未交付。

## 验证与变更

### Phase 5A：统一开发组合

统一入口为 `app.dev_composition:create_app`，按 `PAPERPILOT_A_MODE`（`mock` / `persistent` / `real`）、`PAPERPILOT_C_MODE` 和 `PAPERPILOT_D_MODE`（`mock` / `real`）显式装配现有 DataBinding、RealAI 与 SecurityAdapter。`persistent` 和 A 的 `real` 均使用 A 当前 PostgreSQL 实现；默认均为 Mock。生产模式仍由现有配置拒绝。D 的默认策略适配器没有执行器，允许的 SQL 查询仍为 `allowed/not_executed`。入口不接受客户端指定的适配器或连接，不自动迁移、建立身份或启动 outbox 投递器。

审计边界可以显式设为 `PAPERPILOT_AUDIT_PREFLIGHT=required`，并用 `PAPERPILOT_AUDIT_TRUSTED_OWNER` 指定经过独立审查的审计视图 NOLOGIN 所有者；这要求 A 使用提供 `database.transaction()` 的 PostgreSQL 绑定及专用受限业务身份。共同业务入口在启动、健康检查和每次安全查询前使用该业务身份重新核验审计边界，失败则拒绝启动或以 `AUDIT_UNAVAILABLE` 拒绝查询，不回退 Mock。健康接口的 `limitations` 会标明检查是 `required` 还是 `disabled`。默认的 `disabled` 只适用于开发或隔离测试，不能作为正式安全验收；生产模式仍被拒绝启动。

此检查与后续查询使用不同事务，不能消除两者之间并发权限或 DDL 变更的时间窗口，也不能独立证明项目隔离。正式部署仍需约束谁能更改角色、视图和绑定，并通过数据库角色及项目隔离的独立验收。运维手动只读预检命令是 `python scripts/check_audit_boundary.py --trusted-owner <已审查的角色名>`；数据库连接由安全环境注入，禁止在命令或文档中写入凭据。

开发环境按需安装仓库现有锁定依赖：`.\.venv\Scripts\python.exe -m pip install -r requirements.lock -r requirements-day0.lock`；C 的本地嵌入可选依赖与模型准备见 [C 配置](ai-adapter.md)。不要把凭据写入源码或日志。

准备好明确指定的开发 PostgreSQL 后，设置 A 文档中的 `PAPERPILOT_DATA_HOST/PORT/USER/PASSWORD/DBNAME`，只对已确认的目标执行 `python scripts/manage_postgres.py migrate`、`python scripts/manage_postgres.py seed-demo` 和 `python scripts/manage_postgres.py check`。迁移拒绝无法识别的非空库；上线数据库和受限角色须由 A/D 决定。本入口只做 readiness，不执行这些命令。

在仓库根目录设置各模块模式及 `PAPERPILOT_STORAGE_DIR` 后启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.dev_composition:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

例如：A 持久化且 C/D 模拟使用 `PAPERPILOT_A_MODE=persistent`、`PAPERPILOT_C_MODE=mock`、`PAPERPILOT_D_MODE=mock`；若设 C 为 `real`，还需显式配置 [C 模型与嵌入设置](ai-adapter.md)，并让 C 和 B 共用同一受控上传目录；单次 AI 操作期限必须小于 A 的任务租约，否则启动拒绝。任务 worker 的总等待时间取 90 秒与租约的 80% 中较小值；过期租约无法提交结果，取消等待不代表远程模型未收到请求。若设 D 为 `real`，仅启用策略，不会启用真实 SQL 执行。`GET /api/v1/health` 会检查 A readiness、已知 worker 领取故障及模式；不能证明远程模型或 SQL 能力已验收。

A 的领域事件需独立运行 outbox 投递器，例如在配置相同的可信环境设置 `PYTHONPATH=backend` 后运行 `.\.venv\Scripts\python.exe -m app.data.deliverer --once`；持续运行时省略 `--once`。业务服务启动不会自动启动投递器，也不监控独立进程。迁移 `002_outbox_dead_letter.sql` 必须由运维对明确指定的数据库显式执行，应用启动不会自动迁移；迁移前备份并核对目标，版本不符拒绝就绪。回退需要维护窗口和备份：旧版本不理解死信状态，不可直接回退并继续运行旧投递器。

投递器领取时以数据库时间设置 30 秒租约并递增尝试次数，超时后可重新领取；旧尝试不能确认或改变新尝试的状态。投递为至少一次，审计通过稳定 event_id 去重；其他下游必须自行提供幂等。默认失败达到 5 次进入死信；`OutboxDeliverer.dead_letters()` 仅供受控运维进程读取元数据，不提供面向用户的管理接口。迁移 004 后，已授权运维人员可显式运行 `python -m app.data.deliverer --requeue-event <事件UUID>`；结果未知时必须先按事件 ID 对账，并额外传入 `--confirm-unknown-reconciled`。重投保留事件 ID 和不可变操作记录，但参数只记录操作员的对账声明，不能证明外部系统的副作用已完成。当前无自动清理或告警；独立投递器及运维数据库身份的最小权限尚未完成正式验收。

Windows 隔离回归使用 `powershell -NoProfile -File scripts/test-phase5a-isolated.ps1`；脚本创建自己专用的短生命周期 PostgreSQL 容器和临时数据库，执行 A/B 集成、共享契约、独立浏览器测试，并在结束时关闭该容器。它使用随机的仅测试口令、随机宿主数据库端口和临时上传目录；不会选择现有业务数据库。可通过 `-ApiPort`、`-WebPort` 修改浏览器测试端口；测试结束后临时文件目录由操作系统管理。实际测试结果和平台限制见 [验证记录](verification.md)。

### 安全展示与关联阅读（本轮修订）

研究页使用现有项目论文列表与详情 API 建立“项目文献 → 来源摘录 → 研究助手”联动。点击问答引用或证据来源，会读取对应论文及 chunk，并保留原来源抽屉；迟到响应不会覆盖后来选择的文献。引用的 chunk 已不存在时明确提示，不替换成其他段落。当前定位只有片段与页码，没有 PDF 坐标定位；公共数据不含作者字段，页面不补造作者。

助手按问答、证据、报告切换，保留原生成、证据读取、保存及版本功能。将来源加入报告会复制现有 Source 快照并分配未占用的引用编号，同一来源不重复添加；仍标记待人工核验，不新增“已核验”语义。切换项目或离开研究路由时，有未保存正文/版本差异须选择继续编辑或放弃。切换后按新项目重读数据；这不是未保存编辑的跨刷新恢复，也不是全部聊天记录的持久化。原 Phase 2C 任务恢复机制不变。

安全页通过现有项目 API 核对当前角色和范围，提交查询前再次读取；这只是界面提示及减少无效提交，服务端每次请求的认证/授权仍是实际边界。查询链路是职责示意，不是内部步骤实时遥测。查询结果继续区分策略允许、拒绝、执行失败与未执行；Mock 执行结果始终注明未执行真实 SQL。审计增加操作者与项目：当前身份显示名称，其他身份显示返回的用户标识，不推测姓名。

安全验证面板读取 `frontend/public/security-evidence.json`。在根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/export_security_evidence.py
```

脚本仅在隔离 pytest fixture 中执行约定的匿名访问、跨项目/成员授权、网关故障/关键审计测试，不探测正在运行的服务或真实数据库。输出含执行时间、Git 提交、是否含未提交修改、所检查后端文件指纹、各组 selector 和通过/失败/跳过数；无测试报告时不覆盖原记录。面板是离线证据，不能证明当前部署等同于所测版本。发布前应重新生成并核对版本；报告缺失或无效显示未验证，重新读取按钮不运行攻击或测试。真实 SQL AST/受限执行待 D 验收，真实数据库隔离/恢复待 A 验收。

本轮新增浏览器验证在 `frontend/tests/connected-workspace.spec.ts`；仍使用既有 Playwright 配置和隔离 Mock 后端。默认演示回归继续使用 `frontend/playwright.demo.config.ts`。失败草稿、缺失来源及失败验证报告等场景使用测试内响应夹具，不是实际 AI 或数据库实验。实际命令及数量见 [验证记录](verification.md)。公共端口、OpenAPI、生成类型、锁文件和 A/C/D 注册方式没有变化。

每次提交至少提供正常路径、异常路径、运行说明及限制。修改公共契约时顺序为：Schema/端口 → 文档 → 实现 → 生成 OpenAPI/样例/TS → 测试和构建。当前 Schema 的 mock/synthetic 默认值用于开发，真实实现必须显式覆盖。

所有业务状态以未来 A 的存储为准；前端不把本地临时状态当作数据库成功。授权、来源和迁移相关变更需相关负责人共同复测。

## Phase 2B SQLite 实验现状（历史）

以下仅记录当时实验：`app.persistence.SQLiteData` 曾通过工厂绑定 `persistent`，当前绑定已解除，不能按旧方式启动。SQLite 使用标准库；代码及隔离实验 provider 保留。历史配置、迁移和结果见 [基础持久化](persistence.md)，当前注册以本文及中立接入说明为准。

历史 SQLite provider 曾实际验证数据库门槛和进程重启；这不是 A 正式实现的验收。默认 Mock 仍跳过真实数据库门槛，正式引擎尚未决定。C 的真实内容和 D 的真实网关仍待成员实现。
### Phase 5D：D 策略模式联调

运行 `./scripts/test-phase5a-isolated.ps1` 可在专用临时 PostgreSQL 容器中验证 A 数据层与 D 真实策略、审计及前端状态。测试使用 Mock C，D 的受限 SQL 执行器尚未注册；`allowed/not_executed` 是策略通过但未执行，不是统计结果。测试会使用自己创建的临时数据库与容器，不得指向开发或未知数据库。实际结果和 A/D 后续交付见 [验证记录](verification.md)。


### Phase 5D-4：隔离测试候选执行器

默认 `PAPERPILOT_SECURITY_EXECUTION=disabled`，常规 D 策略模式不执行 SQL。候选只可用于专用隔离测试库，要求 `PAPERPILOT_ENV=test`、`PAPERPILOT_A_MODE=persistent`、`PAPERPILOT_D_MODE=real`、`PAPERPILOT_SECURITY_EXECUTION=candidate`、`PAPERPILOT_SECURITY_ISOLATED_TEST=1`，并从安全环境单独注入 `PAPERPILOT_SECURITY_DB_PROJECT_ID`、`PAPERPILOT_SECURITY_DB_HOST`、`PAPERPILOT_SECURITY_DB_PORT`、`PAPERPILOT_SECURITY_DB_DBNAME`、`PAPERPILOT_SECURITY_DB_USER`、`PAPERPILOT_SECURITY_DB_PASSWORD`、`PAPERPILOT_SECURITY_DB_VIEW_OWNER`、`PAPERPILOT_SECURITY_DB_STATISTICS_SHA256` 和 `PAPERPILOT_SECURITY_DB_TOPICS_SHA256`。主机只接受本地地址，安全账号须与数据层账号不同；凭据不得写入仓库。

两个摘要为 PostgreSQL 16 下各视图 `pg_get_viewdef(oid, false)` 文本的 UTF-8 SHA-256，现须与版本控制中的 `app/security/view_baseline.py` 一致；该基线从隔离环境的仓库迁移生成，**尚待 A/D 审查**。只从被测数据库取值并立即信任无法证明视图安全；PostgreSQL 主版本或视图定义变化需要重新审查和生成。候选需显式安装单独的 `app/security/migrations/002_binding_verifier.sql`，将函数和视图所有者设为同一个不可登录的受限角色，并仅向受限查询登录授予函数执行权限。应用不会自动运行迁移或授予权限。绑定核验函数只判断 `SESSION_USER` 与传入项目是否存在绑定，不向查询登录开放绑定表；其函数体、所有者、搜索路径、公开授权和执行权限被检查。此对象及其所有者权限仍需 A/D 审查。

空项目通过绑定函数区别正确与缺失绑定。预检和查询在只读事务中显式使用 `pg_catalog, public` 搜索路径；仅对匹配受信模板的查询，用固定包装使绑定判断和结果读取共享同一 PostgreSQL 语句快照，并保持原有排序。结果返回前还会再次核验绑定；最后一次核验后仍可能并发变更，这不证明即时撤权。函数执行权限、其他对象和函数、视图所有者对所有依赖的最小权限及生产配置仍需安全审查。此实现只限隔离测试，不能作为正式数据库安全验收。

候选执行器仅为当前三个统计意图显式绑定排序字段；D 将来新增允许模板时，必须同步审查并更新候选映射及测试，否则执行器拒绝该模板。

Phase 6.2-B 的预检还核对实际视图依赖关系、绑定表主键及已验证的项目外键、受限登录和所有者的 NOINHERIT/成员关系、允许视图的列级写权限、序列权限和额外非系统 SECURITY DEFINER 函数授权。这些是限定范围的失败关闭检查：没有对所有 PostgreSQL 内置函数、普通函数、扩展、其他账号授权或并发撤权做完整安全证明。不可登录所有者及数据库外的可信基线仍需 A/D 审核。

安全查询在路由内因未认证或项目授权失败而被拒绝时，会向 DataPort 写入 `secure_query.access_denied`，记录请求编号、已验证的用户（匿名时为空）、拒绝和未执行状态；不记录问题、令牌或未经授权的项目标识。项目字段固定为空，避免将不存在或不可访问的项目写入外键而产生可区分结果；因此这类记录目前不会出现在按项目过滤的审计页面，需要 A/D 确认系统级审计读取和保留方式。审计写入未确认时返回 `AUDIT_UNAVAILABLE`，不调用网关。此覆盖仅针对通过请求格式校验并进入安全查询路由的请求，CSRF 和格式校验等更早的拒绝尚未覆盖。

Windows 下可运行 `powershell -NoProfile -File scripts/test-phase6-security-browser.ps1` 验证候选的隔离浏览器链路。脚本创建并关闭专用临时 PostgreSQL 容器；管理员仅用于准备测试库、视图、账号和绑定，浏览器统计请求使用专用受限登录。它测试真实执行、拒绝、服务中断、重启后审计，以及使用浏览器会话发起的跨项目和撤权请求；C 为 Mock，开发身份不代表正式认证。测试视图摘要在夹具中生成，不能代替 A/D 对正式配置的独立审查。不得用此脚本指向现有业务库。

准备好独立测试库、迁移、项目绑定和受限角色后，设置 `PYTHONPATH=backend` 并运行 `python -m app.selector_server --host 127.0.0.1 --port 8000`。此入口在 Windows 为整个服务器创建 Selector 事件循环，解决 psycopg 异步连接不支持默认 Proactor 的限制；常规 uvicorn Windows 入口没有获得此兼容保证。应用启动时预检受限身份，失败即拒绝启动并关闭数据资源；不可恢复的连接或权限错误令健康检查失败。`/api/v1/health` 的 `limitations` 标明候选启用及就绪；`security_executor_not_managed` 仅表示此生命周期未管理候选，不能证明外部注入的执行器关闭。

完整 JSON 响应正文超过 256 KiB 时返回 `QUERY_RESPONSE_TOO_LARGE`，不返回截断成功结果。检查发生在执行和审计之后；审计显示已执行不等于客户端收到结果。执行器自身另有限制列/行 JSON、数据库超时与行数。`powershell -NoProfile -File scripts/test-phase5a-isolated.ps1` 在专用临时容器测试真实数据库候选和独立服务进程；浏览器仍只覆盖策略未执行模式。正式执行保持关闭，须先完成 A/D 联合审查。

### Phase 6.5：凭据和会话（候选，非生产验收）

应用环境变量必须显式设置 `PAPERPILOT_AUTH_MODE=development` 或 `credentials`。前者仅允许开发及测试环境，是用户名切换演示身份；后者要求 PostgreSQL 数据绑定和迁移 003，凭据或存储不可用时不会回退演示身份。`PAPERPILOT_SESSION_IDLE_SECONDS`（默认 1800）和 `PAPERPILOT_SESSION_ABSOLUTE_SECONDS`（默认 28800）控制空闲与绝对有效期。生产环境仍明确拒绝启动。

只对明确指定的数据库按既有流程显式执行迁移，不自动创建密码。受授权运维人员设置 `PYTHONPATH=backend` 和数据库连接环境后，运行 `python scripts/initialize_auth_password.py --username <已有用户名> --confirm-database <已确认数据库名>`，通过终端交互输入两次密码；已设置密码的用户不会被覆盖。明确使用 `--replace-existing` 才能重设密码，并在同一事务撤销该用户已有会话。不要将密码或数据库口令写入脚本、命令行或仓库。

认证使用 Argon2id 密码摘要和随机 Cookie 会话令牌；数据库保存令牌摘要，停用、撤销、空闲及绝对过期在请求时查询数据库。可信浏览器来源必须列入 `PAPERPILOT_ALLOWED_ORIGINS`；凭据模式的写请求必须带可信 Origin、`/api/v1/auth/csrf` 提供的标记及对应 Cookie。凭据登录及退出的终态审计与会话变更同一 PostgreSQL 事务；失败前置审计单独记录，数据库提交结果未知仍需对账。上线还需要审查 TLS、Cookie Secure、会话访问控制、系统级审计读取和正式数据库身份。

迁移 005 增加跨 worker 共享的登录限速表。每个 ASGI 客户端地址对所有账号的尝试共用固定时间窗，默认 `PAPERPILOT_AUTH_LOGIN_LIMIT=10`、`PAPERPILOT_AUTH_LOGIN_WINDOW_SECONDS=300`；成功与失败均计数，超过限额返回 429 并写入不含账户、密码或令牌的拒绝审计。失效、过期或停用的已知会话被请求时记录拒绝事件；未知令牌不会被归属于某个用户。限制依赖 ASGI peer：服务端或可信代理可能改写该地址，若代理设置错误可能被伪造，若多用户共用代理地址则会共用限额。正式部署前必须核实代理信任边界、NAT 可用性和限速表保留策略；该功能不能代替独立认证安全审查。审计不可用时拒绝请求。

运行 `powershell -NoProfile -File scripts/test-phase65-isolated.ps1 -Browser` 可在专用临时 PostgreSQL 容器验证会话存储和真实浏览器；测试仅限隔离数据，实际测试结果见 [验证记录](verification.md)。

### 审计数据库边界候选

显式迁移 006 创建项目审计视图及事件指纹函数；迁移不自动创建数据库身份、授予业务角色权限或设置可信所有者。部署运维须独立审核并配置不可登录的受限所有者和业务身份，再用业务身份的 `PAPERPILOT_DATA_*` 环境配置执行 `python scripts/check_audit_boundary.py --trusted-owner <经审查的所有者角色>`。预检检查范围见 `app/data/audit_boundary.py`；它不输出连接凭据，也不修改数据库。失败或无法确定权限时不得进入生产使用。

该视图排除系统级事件，但共享业务连接仍可通过视图读取多个项目事件；项目成员校验仍由可信服务端执行，视图本身不证明数据库原生跨项目隔离。独立预检可如上显式接入启动，默认关闭；系统级审计授权读取及审计角色完整权限仍未完成。

`app/security/migrations/003_candidate_scoped_audit.sql` 是独立隔离测试候选，不由数据迁移器执行。它用专用登录与 `SESSION_USER` 绑定用户和项目，并核对现有项目负责人权限及账户状态。默认应用仍使用共享业务审计视图；下述测试模式可显式启用候选读取。候选尚需正式凭据管理、完整权限及生产审查。撤权测试仅证明 READ COMMITTED 下新语句的行为，不能保证正在执行或长事务的旧快照立即撤销。

### Phase 6.7：多身份隔离审计接入（仅限测试）

显式设置 `PAPERPILOT_SCOPED_AUDIT=isolated`、`PAPERPILOT_SCOPED_AUDIT_ISOLATED_TEST=1` 和 `PAPERPILOT_AUDIT_PREFLIGHT=required`，并使用测试环境、凭据认证和真实 PostgreSQL 数据绑定，方可从统一组合入口注册独立审计读取。其余 `PAPERPILOT_SCOPED_AUDIT_*` 的字段名见 `.env.example`；凭据通过受保护的进程环境注入，配置及日志中不可写入真实密码。数据库目标必须与业务连接一致，读取身份必须不同，当前仅允许本机隔离数据库。用户和项目 ID 来自可信服务端配置，不接受请求携带的映射。

多用户隔离测试可由受保护的环境变量 `PAPERPILOT_SCOPED_AUDIT_BINDINGS` 注入 JSON 数组；每项仅包含 `db_user`、`db_password`、`user_id` 和 `project_id`，至多 64 项，数据库连接目标、视图所有者和绑定表所有者由单独配置指定。不能同时设置旧的单用户字段；重复账号、重复用户/项目、不同数据库目标、无效 JSON 或缺少字段均拒绝启动。请求只能路由到与服务端会话及项目一致的独立数据库登录，每次使用并关闭自己的连接；数据库再次验证绑定。不要在命令行、终端历史、仓库或日志写入真实数组。环境变量并非完善的生产秘密管理服务，具备进程环境读取权限的主体仍可能看到凭据。

当前配置在进程启动时固定，不提供在线更新：在明确授权的隔离数据库中，由运维先创建并验证新角色/新绑定，再通过受保护渠道向新实例注入更新配置；确认新实例就绪、旧实例排空后，撤销旧绑定或账号。所有实例必须应用相同的受信配置；不能仅更新一个 worker 后声称全局轮换完成。成员撤权和绑定撤销后，新的数据库语句拒绝访问，但无法取消已开始的查询。启动预检和健康检查会检查全部已配置映射；某个映射故障使全局健康检查失败。安全查询仅检查当前项目的映射，其他项目的独立请求不会因该映射被直接路由至错误身份。

启动、健康检查及安全查询前验证读取身份；审计 API 再按会话及实时成员关系授权，并在独立短连接内复核数据库绑定。每次请求关闭连接，不跨用户或项目复用连接。开启 `PAPERPILOT_AUDIT_PREFLIGHT=required` 时，业务身份必须不能读取旧的共享审计视图；缺失配置、权限漂移或绑定不匹配时失败关闭，不回退共享读取。预期视图定义摘要和函数体来自版本控制代码及候选迁移，针对 PostgreSQL 16、固定 `pg_catalog` 搜索路径；改变版本或定义时需重新审查，不能从被检查的部署数据库自行信任生成值。

`powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1` 自动启动独立临时测试容器，执行审计与浏览器测试；`-ScopedOnly` 只执行新接入组。正式环境不可使用该候选：正式秘密管理和多实例在线轮换、独立安全审查、基表所有者和依赖对象的完整核验、完整最小权限和可靠部署仍未就绪；业务连接的项目访问并未实现数据库原生 RLS。读取采用同一只读事务快照，撤权不能取消已开始的查询，DBA 对授权、DDL 及绑定的并发修改仍须受控。


### Phase 6.7 隔离候选：多项目统计与认证连接拆分

仅供显式指定的本地隔离 PostgreSQL 测试，生产模式仍拒绝启动，真实 SQL 默认关闭。多项目统计在原候选所需的公共数据库主机、端口、库名、视图所有者和固定摘要之外，通过受保护的进程环境注入 `PAPERPILOT_SECURITY_DB_BINDINGS` JSON 数组：每个元素严格包含 `project_id`、`user`、`password`，每个项目及数据库登录唯一，最多 64 项。数组与旧的单项目 `PAPERPILOT_SECURITY_DB_PROJECT_ID/USER/PASSWORD` 不得同时配置；原单项目配置保持可用。服务端依据已授权项目上下文路由，独立账号逐次连接并校验数据库绑定；启动和健康检查校验所有项目。凭据轮换需受控重启并逐实例核验；当前没有集中密钥服务、在线热更新或多实例原子轮换。环境变量可能被拥有进程查看权限的用户读取，不是正式密钥管理方案。

认证分离候选需 `PAPERPILOT_ENV=test`、`PAPERPILOT_AUTH_MODE=credentials`、持久数据层和 `PAPERPILOT_AUTH_DATABASE=isolated`，此外显式注入 `PAPERPILOT_AUTH_DB_ISOLATED_TEST=1`、`PAPERPILOT_AUTH_DB_HOST/PORT/DBNAME/USER/PASSWORD`。认证与业务必须指向同一隔离本地数据库但使用不同登录身份，业务身份不能读取密码摘要或会话表；统计和审计身份不得与认证身份相同。启动、健康检查及每个认证事务前检查隔离与权限，检查失败拒绝访问。配置缺失不回退共享身份。密码通过部署环境的受保护机制注入，勿写入版本控制和日志。

后续候选使用 `backend/app/security/migrations/004_auth_lock_candidate.sql` 中固定定义的锁定函数；由操作员在明确的隔离库中显式应用，配置独立可信的 `PAPERPILOT_AUTH_LOCK_OWNER`。该所有者必须为不可登录、无成员关系的角色，函数只能由认证身份调用；认证身份可以读取密码摘要，但不得直接更新用户字段。启动及每个认证事务检查受版本控制的函数体、签名、所有者和权限。运维密码重置仍必须使用受支持的事务流程，在同一数据库中更新用户行并撤销会话，不能绕开此规则。预检与使用不在同一事务，管理员并发更改 DDL/权限的窗口、所有系统函数与部署身份仍需独立审查。部分旧隔离夹具仍使用宽权限；新联合测试启用表操作权限基线，但不代表正式数据库角色或项目行隔离已获验收。


受保护文件输入支持 POSIX 与严格受限的 Windows 本地文件：`PAPERPILOT_DATA_PASSWORD_FILE`、`PAPERPILOT_AUTH_DB_PASSWORD_FILE`、`PAPERPILOT_SECURITY_DB_PASSWORD_FILE`、`PAPERPILOT_SECURITY_DB_BINDINGS_FILE`、`PAPERPILOT_SCOPED_AUDIT_PASSWORD_FILE`、`PAPERPILOT_SCOPED_AUDIT_BINDINGS_FILE`。必须为绝对路径、常规文件、当前进程用户拥有且无 group/other 权限，拒绝符号链接和超过 64 KiB 的内容；同名变量与 `_FILE` 同时提供会拒绝启动。Windows 仅接受本地固定磁盘上的普通文件：所有者必须是当前有效用户，DACL 必须关闭继承且只含授予该用户的简单显式允许项；其他账户授权、未知 ACE、可写共享冲突、重解析点和不满足要求的路径均拒绝。Windows 管理员、系统权限与同一账户的其他进程仍属于信任边界，该方式不是安全保险库。按现有流程显式、安全地供应受限文件；进程启动时读取，更新文件不会热更新已有连接，需启动新实例、验收、排空旧实例再撤销旧凭据。该本地文件机制不是生产密钥服务，容器挂载、所有者、备份、轮换和 Windows ACL 仍需具体环境验证。

认证锁候选的隔离环境准备顺序：由隔离数据库管理员显式执行 `backend/app/security/migrations/004_auth_lock_candidate.sql`；创建单独的 NOLOGIN 锁函数所有者，授予 `public.users` 的 `user_id/password_hash/disabled` 三列读取及 `password_hash` 列更新权限，临时授予 `paperpilot_auth` schema CREATE 用于转移函数所有权后立即撤销；只给认证登录 schema USAGE、该函数 EXECUTE，以及原会话、限速和审计写入所需权限。业务登录不能读取密码摘要或访问会话表。`PAPERPILOT_AUTH_LOCK_OWNER` 必须由可信部署配置显式给出；上述 SQL 文件不会由应用自动执行，重复执行会失败，需要操作员先检查对象与版本，不能直接覆盖现有函数。完整隔离示例及拒绝测试见 `backend/tests/test_authentication_boundary_postgres.py` 的 `install_auth_lock`，只应对明确创建的隔离库运行。预检不阻止可信数据库管理员并发 DDL，正式环境需额外审查权限变更流程和运维身份。


`PAPERPILOT_BUSINESS_PRIVILEGE_PREFLIGHT=required` 是隔离环境的候选检查：仅允许 `PAPERPILOT_ENV=test` 并要求审计预检开启；业务数据库的每个事务入口（包括后台 worker）、启动、健康及安全查询预检会按 `backend/app/data/business_boundary.py` 中的表操作清单检查有效授权，拒绝意外的 DELETE、TRUNCATE、REFERENCES、TRIGGER、额外列授权和指定危险函数。业务服务及 worker 使用同一业务身份，因此此检查按两者联合需要赋权；outbox 仅插入，单独投递身份的更新权限仍需另行配置。清单校验的是表操作而非逐列最小化或数据库原生项目行隔离，不能代替 RLS、业务身份隔离和生产审查；新增业务 SQL 应同步审查清单。

Windows 测试模式的文件权限必须为当前实际运行身份专属。以该身份使用 Windows PowerShell 为已有秘密文件设置受保护 DACL（`$taskSecretPath` 仅为操作员提供的文件路径；不要把文件内容写入脚本或版本库）：

```powershell
$taskSecretPath = 'C:\path\to\protected-secret'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$taskAcl = [System.Security.AccessControl.FileSecurity]::new()
$taskAcl.SetOwner($taskUser)
$taskAcl.SetAccessRuleProtection($true, $false)
$taskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($taskUser, 'FullControl', 'Allow'))
[System.IO.File]::SetAccessControl($taskSecretPath, $taskAcl)
```

将对应 `_FILE` 环境变量设置为该文件绝对路径，且不要同时设置对应明文变量。文件应位于可控目录，本地管理员、系统进程、相同运行账户下的其他进程，以及拥有备份/调试特权的主体仍需纳入威胁模型。权限不满足时应用拒绝启动或读取。Windows 隔离测试使用 Windows PowerShell/.NET ACL 创建临时测试文件；本实现尚未经独立 Windows 安全审查。

隔离容器联合验证可在本机已构建的测试镜像上运行 `powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -ScopedOnly -ContainerImage paperpilot-phase67-guard-test`。脚本仅创建本轮独立 PostgreSQL 容器并用它的网络命名空间启动显式本地镜像；管理员身份用于准备测试数据和授权，应用使用分离的业务、认证、统计和审计测试身份。容器测试凭据是临时测试值，通过测试进程环境注入；它不是生产秘密挂载验收。测试会执行真实允许查询并重建应用容器后重读会话与审计，然后只停止本轮创建的容器。

### 业务数据行隔离试验（仅限独立测试库）

`backend/app/security/migrations/005_candidate_business_rls.sql` 是独立候选脚本，不属于现有自动迁移链，也不能用于现有应用或正式数据库。它对 22 张具有项目标识的表启用强制 RLS，以数据库 `SESSION_USER` 在受保护绑定表中映射到唯一用户和项目；成员删除时外键级联撤销绑定。不能用客户端输入或连接会话变量扩大该数据库身份的范围。可信绑定需要由单独的受控运维身份供应，脚本及预检依赖 PostgreSQL 16，必须先审查并确认版本。重新运行候选脚本并非幂等操作，不得在未知数据库执行。

`backend/app/data/scoped_business.py` 不是完整 DataPort：它验证绑定、RLS 策略、函数定义和部分权限，用户表仅允许读取身份展示的三列。`ScopedBusinessRouter` 可在隔离测试中显式传给 `create_app(..., scoped_business_reader=router)`，以经认证的服务端用户和项目选择只读身份；项目论文、来源、任务、报告及版本的 GET 经过该身份，不存在身份时失败关闭。此入口仅允许 `environment=test`，没有接入默认组合入口，也不提供正式部署配置。试验期间禁止其他业务 API 并停用后台 worker；身份和项目授权依然调用既有服务，不代表其本身已经迁移到受限身份。隔离验证在仓库根目录执行 `powershell -NoProfile -File scripts/test-audit-candidate-isolated.ps1 -BusinessOnly`；该命令新建专用临时 PostgreSQL 容器并仅关闭它自己创建的容器。管理员只负责测试准备，读取和越权尝试使用不同的受限测试身份。

当前仍未实现按项目切换的完整读写业务接入、项目创建和全局任务领取的受控数据库身份；对 22 张表的列级授权仍需进一步收紧。部分全局身份表、用户停用时的数据库凭据撤销、所有函数及系统对象授权、生产凭据供应与轮换，以及并发权限变更的安全审查仍未完成。不可将此候选用于证明整个业务 API 已经具备数据库原生项目隔离；真实 SQL 执行仍默认关闭。撤权只保证后续语句重新检查，无法撤销已经执行的语句或结果。

`006_candidate_business_actor_checks.sql` 在仅供隔离测试的 005 基础上，为导入论文、任务、幂等记录和 outbox 的写入增加与登录用户的绑定校验；现有项目范围读取不受该附加检查影响。`ScopedBusinessWriter` 是独立验证用的上传及任务创建候选，预检要求六张表的确切 INSERT 列清单以及受信的操作者检查；禁止 UPDATE、DELETE 和未列出的写权限。测试通过 `-BusinessOnly` 在新建隔离 PostgreSQL 容器中应用 005 和 006 并运行，管理员仅准备环境，写入使用受限身份。两份 SQL 均未接入正式迁移链，不是可直接用于已有数据库的迁移。

该写入候选仍未接入 API，也没有实现项目创建或后台任务结果提交：现有 RLS 在项目建立前缺少可信绑定，全局 worker 的领取仍缺少正式的独立数据库身份和逐项目路由。六张表的 INSERT 列清单不是对所有业务数据的完整最小权限证明；数据库权限、服务端认证与操作者关联、角色供应和撤销仍需完整审查。尤其不能把候选视为完整审计防伪或正式安全验收结果。

在隔离候选中，显式应用 `007_candidate_report_actor_checks.sql` 后，`ScopedReportWriter` 还可保存报告及版本快照：新报告和版本的作者由数据库约束为登录用户，允许同项目其他成员编辑已有报告；仅对报告当前版本指针授予列级 UPDATE。数据库原有版本链约束仍适用。该类尚未接入 API，INSERT 字段仍需进一步审查，不能把测试结果视作完整写入权限验收。

显式应用 `008_candidate_worker_actor_checks.sql` 后，`ScopedClaimWorker` 可在隔离库中按预配置的用户和项目领取任务，采用既有数据库锁和租约机制；它仅包含领取，不具备结果提交或失败处理能力，也尚未接入后台 worker。`PostgresData` 默认保持全局领取行为，只有显式提供服务端可信范围的候选实例才收窄领取条件；不能将该候选作为正式调度器。绑定撤销后新领取失败，已启动的处理不会被自动取消。007/008 与 005/006 一样只供显式隔离测试，不属于应用自动迁移链。

`ScopedFailureWorker` 在隔离测试中增加失败状态及 outbox 写入；`ScopedResultWorker` 另外依赖显式执行的 `009_candidate_worker_result_checks.sql`，通过调用者权限函数和限制性 RLS 在写入结果、证据、来源及片段时检查用户、项目和有效租约。预检核对函数体、所有者、执行权限及策略；无配置或校验失败时拒绝。候选继续使用逐用户/项目身份，没有注册到应用或后台调度器，也未解决用户撤权后尚存任务的托管策略。009 不属于自动迁移链，不应应用于现有数据库。

该候选的列级 UPDATE 授权和 RLS 不等于数据库已完全强制所有状态机约束：例如受限凭据持有者仍可能直接构造项目内的状态更新；需要独立审查并设计受控状态转换入口后，才能考虑正式接入。片段、证据真实性依赖 C 和应用层验证，不能因数据库策略而视为已核验。

`010_candidate_project_bootstrap.sql` 和 `ScopedProjectCreator` 是仅限隔离库的项目创建候选。专用登录与用户通过受保护的 `bootstrap_logins` 绑定，登录自身不能直接访问业务表；固定的 `SECURITY DEFINER` 创建函数归独立 NOLOGIN 角色所有，根据 `SESSION_USER` 和账户停用状态确定创建者，并在同一事务写入项目、负责人关系及 outbox。预检检查身份、函数定义、部分策略及列级权限，错误配置失败关闭。测试通过 `scripts/test-audit-candidate-isolated.ps1 -BusinessOnly` 在新建容器中运行；该候选没有注册到默认组合或正式 API，010 不属于自动迁移链。

创建后的项目用户数据库账号及项目绑定仍须由可信运维/供应流程建立，当前不会自动供应、轮换或撤销凭据；候选新增的 RLS 策略也尚未与所有其他候选联合预检。现有公开项目创建接口的未知提交结果缺少可重放的幂等凭据，不能盲目重试。绑定供应、角色与所有依赖对象、状态转换和正式部署仍需独立审查，不能将测试结果作为完整生产隔离证明。

仅限隔离测试的 `011_candidate_project_replay.sql` 和 `ScopedReplayProjectCreator.create_project_with_key` 为未来接入准备独立操作键：键与用户绑定，重放相同输入返回已有项目，键相同但内容不同时冲突；记录、项目、成员和 outbox 原子提交。请求编号只用于追踪，同一请求编号不会去重，冻结的 `DataPort.create_project` 和现有 API 行为没有改变。该候选依赖 010，不自动迁移、不向 HTTP 暴露；要支持浏览器未知提交恢复，仍需单独审查兼容的操作键传递、前端持久化及失败恢复流程。隔离测试在真实提交后用连接替身模拟确认丢失，并在新实例以原键重放；没有注入真实网络断线或进程崩溃。

数据库写事务在执行 COMMIT 期间发生非完整性约束错误时返回 `DATA_COMMIT_UNKNOWN`，即使后续 rollback 调用成功，也不会据此认定事务未提交；该连接会被丢弃。已确认的约束冲突继续返回 `IDENTIFIER_CONFLICT`。没有支持重放的操作不能根据未知提交结果自动再次创建；使用操作键的候选仍需重新认证和预检。提交故障分类的验证使用连接替身，不代表已在真实网络上注入确认丢失故障。

隔离候选组合时，业务预检允许 010 中三个明确限定到 `paperpilot_bootstrap_executor` 的创建策略，并核对策略表达式、角色不可登录与角色成员关系；缺少任一策略、角色不安全或策略发生变更时拒绝就绪。创建函数所有者还必须获得 `bound_project()` 和 `bound_identity(uuid,uuid)` 执行权限：006 的 outbox 策略可能调用后者。创建候选预检对这两个函数核验独立迁移中的函数体、所有者、安全属性和权限。这些要求只用于隔离候选，不是正式数据库角色供应方案；每个候选仍须分别通过自己的预检和独立安全审查。

隔离测试入口可显式在 `ScopedBusinessRouter` 注册 `ScopedBusinessWriter`，使上传 API 通过用户和项目绑定的受限身份写入论文和任务；缺少对应写身份时拒绝，不回退共享业务连接。仅允许测试环境显式注入，后台 worker 仍停用，其他写入接口继续拒绝。当前认证、项目成员检查和审计调用仍来自原应用依赖，并非完整数据库原生隔离；测试中相关身份和审计可能使用替身，不能据此宣称正式部署或完整认证、审计联测通过。健康接口通过 `scoped_business_upload_pilot` 标记此能力。

同一测试入口可显式注册 `ScopedReportWriter`：报告引用先通过该身份读取和校验，之后通过相同身份写入版本和引用快照；缺少对应身份时拒绝，健康接口标记 `scoped_business_report_pilot`。审计仍使用应用原有审计服务，审计失败时不会向客户端返回成功；写入提交后的审计失败不代表数据库已回滚，应使用原幂等键核对。该候选尚非正式部署。

后台 worker 仍不得直接接入：005 的 `bound_identity` 检查登录与成员绑定，并未检查 `users.disabled`。正式会话停用检查不能代替后台进程的数据库身份撤销；账户停用、任务托管、连接及凭据撤销需建立可验证的统一策略，并在受限身份和独立真实故障测试通过后才能启用。不得凭现有 worker 候选的结果测试声称这些条件已完成。

隔离候选 `012_candidate_business_active_identity.sql` 显式在 005 之后替换两个项目绑定函数，依据 `SESSION_USER`、项目绑定与 `users.disabled` 决定项目访问；候选预检以 012 的版本控制内容核对函数，并要求函数所有者对用户表仅有 `user_id` 与 `disabled` 两列 SELECT，不能读取密码摘要或写入用户表。测试夹具显式创建并授权该 NOLOGIN 所有者，012 不属于自动迁移链，不得自动在未知数据库执行。READ COMMITTED 下停用提交后的新语句不再看到项目数据，后续领取和结果/失败提交也被拒绝；已经开始的语句及更强隔离级别下的旧快照不会因此立即撤销。该方案不撤销 PostgreSQL 登录凭据，也不禁止受限身份直接读取原本允许的非项目用户展示列；身份与密钥的供应、轮换、正式撤销、在途任务托管及应用 worker 接入仍未完成，后台 worker 继续关闭。

隔离组合入口 `app.dev_composition:create_app` 可通过 `PAPERPILOT_SCOPED_BUSINESS_ISOLATED_TEST=1` 显式加载业务身份清单；还需指定 `PAPERPILOT_SCOPED_BUSINESS_BINDINGS_FILE`、`PAPERPILOT_SCOPED_BUSINESS_FUNCTION_OWNER` 和 `PAPERPILOT_SCOPED_BUSINESS_BINDING_OWNER`。必须是 `PAPERPILOT_ENV=test`、持久化数据模式及明确配置的本机隔离数据库，清单只能来自受保护文件，不能使用明文环境变量。Windows 文件必须满足现有当前用户专属 ACL 检查，POSIX 文件必须为当前用户所有且无组/其他用户权限。清单是 JSON 数组，每项必须且只能包含 `db_user`、`db_password`、`user_id`、`project_id`、`profile`；profile 只能为 `read`、`upload` 或 `report`。仅在仓库外的受保护文件填写真实凭据，不将该文件提交或输出到日志。

登录身份和用户/项目对必须分别唯一，且业务、认证、审计和统计身份不能重用；函数所有者、绑定所有者由运维从可信配置指定。该入口不创建角色或绑定，管理员仍需在明确的隔离数据库预先供应符合预检的身份和授权。当前映射在启动时载入；旋转口令应关闭应用和旧连接、由受控运维变更专用角色密码、更新受保护清单并重新启动验证。更改数据库密码本身不会中止已经认证的数据库连接，当前没有自动在线轮换或连接撤销能力。任何身份预检失败时启动或健康检查失败，不回退 Mock；不等于整体系统已具备生产密钥管理。项目创建与身份供应仍不是原子操作，失败或未知提交应先对账，不得盲目重建项目。

`report` 配置目前同时包含上传所需授权，而非纯报告最小权限身份；不同 profile 的完整列级权限和正式供应方式仍需独立审查。受限业务试验依旧停用后台 worker、阻止尚未接入的路由，也不能替代正式身份与审计的全链路验收。

测试代码可显式向 `create_app` 注入 `scoped_workers`（各项必须是独立数据库登录的 `ScopedResultWorker`）和 `scoped_worker_audit` 回调，以试验受限 worker 的启动、领取和关闭。此路径只允许 `environment=test`、已有受限业务路由及 Mock AI，审计回调必须显式提供；没有通过环境清单或默认组合入口自动启用。每个 worker 的用户/项目必须有对应业务路由，数据库角色不得与任何业务路由角色复用；启动前所有 worker 均须通过预检。健康结果标记 `scoped_worker_mock_pilot`，处理出的 Mock 片段是合成数据。没有显式注入时受限业务模式的 worker 仍关闭。

该试验的审计回调可能是测试替身，不代表审计已持久化；真实 C 服务、独立进程崩溃恢复和自动凭据供应未接入此 worker。账户停用或成员撤销后的新数据库操作失败关闭，不保证终止已经执行中的外部 AI 调用。worker 租约到期与任务托管、显式凭据撤销、正式角色最小权限及四方浏览器联合测试仍须验证，不能用于正式部署。

隔离测试也验证了 worker 使用独立测试审计身份写入真实 PostgreSQL，以及审计权限被撤销时的提交后行为：业务结果和 `complete_job` outbox 已提交，后续 `process.completed` 审计写入失败不会回滚业务；审计恢复后，可由另一个项目绑定的测试投递身份将该 outbox 事件投递一次。两种审计 action 不相同，不能把 outbox 投递当作已补齐原 `process.completed` 审计。投递试验只证明测试身份在特定项目和故障场景的行为，未证明生产投递身份的完整最小权限或进程重启恢复。

高风险未决项：上述测试审计身份被直接授予 `audit_events` 的 INSERT，而现有表没有数据库原生的项目与操作者写入策略；从数据库授权设计看，该身份可能直接构造其他有效项目或用户的事件，应用层 worker 转发检查无法约束绕过应用的 SQL。不得将该测试角色用于正式环境。需要实施并独立审查数据库级审计写入绑定、专用角色最小权限、系统级事件通道及预检后才能考虑正式接入。

仅限隔离数据库的 `013_candidate_worker_audit_rls.sql` 是对该风险的候选修复：为审计表强制 RLS，要求项目和操作者均匹配登录身份绑定，并只允许指定的 NOLOGIN 函数所有者读取事件以校验重放。隔离测试实测原策略下跨项目伪造成功（测试事务已回滚），应用 013 后合法同项目写入成功，跨项目和冒充其他操作者写入被拒绝。013 需要运维预先单独供应和审查固定角色及函数所有权；没有项目为空的系统级审计写入通道，并且改变整个审计表的 RLS，可能阻断现有登录等审计。**不得将 013 应用于正式或未知数据库**，也不能把该候选测试描述为正式审计验收通过。

隔离候选 `ScopedWorkerAuditWriter` 使用单独的用户/项目绑定数据库身份，按受版本控制的 012 和 006 函数定义以及 013 策略、角色和权限预期，在每个审计事务前预检；`ready()` 可作为 `create_app` 的 `scoped_worker_audit_ready` 显式回调，以便在启动和健康检查时发现不一致。检查覆盖 PostgreSQL 16、会话身份、角色属性及成员关系、部分库/schema/表/列/序列和特权函数、审计表 RLS、绑定关系与相关函数定义。检查失败返回审计不可用，不回退共享身份。该回调仅用于显式注入的测试候选，默认组合入口不会自动注册或供应它；它不代表已完成所有数据库对象、所有者、系统审计及生产权限审查，也不能阻止可信所有者在预检后更改配置。正式部署前仍需独立审查与受控供应，并完善系统级审计通道及真实联合验收。

`014_candidate_auth_audit_rls.sql` 是仅供隔离测试的认证完成事件候选，依赖 013 和预先创建的专用 `paperpilot_auth_audit_writer` 登录身份。它只允许该身份写入无项目、非 Mock 的已完成会话事件；限制性策略确保该身份即使被错误绑定项目，也不能借 013 的公共策略写入项目事件。worker 审计预检同时核对这两条策略的角色、表达式及限制性属性；两条必须完整存在且不能被改宽。此候选没有为登录前审计门槛、匿名访问拒绝或其他系统事件提供通道，也没有接入正式应用组合或自动角色供应。认证身份本身仍能构造允许范围内的事件，身份可信性、完整权限、函数和数据库所有者均需独立审查；不能据此声称所有审计防伪已完成。**不要在正式或未知数据库应用 014。**

`015_candidate_system_audit_rls.sql` 与 `ScopedSystemAuditWriter` 是仅限本地隔离测试的登录前和匿名安全查询拒绝审计候选：单独的 `paperpilot_system_audit_writer` 登录只能插入白名单内无项目事件，限制性 RLS 阻止误绑定项目后扩大写入范围。预检核对角色、部分数据库和对象权限、绑定函数及其所有者、015 的版本控制函数体和策略；失败时不回退共享写入。测试可通过 `create_app(..., scoped_system_audit=...)` 显式注入，启动与健康检查调用其预检，关停时关闭连接；该入口仅接受 `environment=test`、凭据认证、持久化数据模式及与业务身份不同的本机数据库身份。`CandidateAuthAuditGuard` 另可显式注册在隔离认证连接上，检查认证角色、审计表 RLS、014 策略及身份绑定；它只是附加检查，不能替代 `AuthenticationBoundary` 的完整预检。

上述候选不自动迁移、不自动创建角色、不进入默认组合，也尚无正式角色供应、密钥轮换与连接撤销流程。测试连接中的审计表和函数可信所有者由隔离数据库管理员担任，仅用于功能验证，不能证明正式最小权限。当前认证预检与 `AuthenticationBoundary` 对 013/014 的完整组合仍未接入；系统写入身份能够构造白名单范围内的事件，不能声称事件来源具备密码学防伪。登录前审计在独立事务中提交：后续会话写入失败时，原 `pending` 事件仍会保留，不代表登录完成。禁止把这些候选应用于正式或未知数据库。
