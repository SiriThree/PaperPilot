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
