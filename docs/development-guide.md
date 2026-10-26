# A/B/C/D 开发及交接指南

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

每次提交至少提供正常路径、异常路径、运行说明及限制。修改公共契约时顺序为：Schema/端口 → 文档 → 实现 → 生成 OpenAPI/样例/TS → 测试和构建。当前 Schema 的 mock/synthetic 默认值用于开发，真实实现必须显式覆盖。

所有业务状态以未来 A 的存储为准；前端不把本地临时状态当作数据库成功。授权、来源和迁移相关变更需相关负责人共同复测。

## Phase 2B SQLite 实验现状（历史）

以下仅记录当时实验：`app.persistence.SQLiteData` 曾通过工厂绑定 `persistent`，当前绑定已解除，不能按旧方式启动。SQLite 使用标准库；代码及隔离实验 provider 保留。历史配置、迁移和结果见 [基础持久化](persistence.md)，当前注册以本文及中立接入说明为准。

历史 SQLite provider 曾实际验证数据库门槛和进程重启；这不是 A 正式实现的验收。默认 Mock 仍跳过真实数据库门槛，正式引擎尚未决定。C 的真实内容和 D 的真实网关仍待成员实现。
