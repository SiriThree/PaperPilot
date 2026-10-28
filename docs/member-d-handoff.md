# 成员 D 开工交接

2026-10-28 已完成 D 的本地开发任务与固定参考验证，见[当前进度](member-d-progress.md)。A 正式 PostgreSQL DataPort、B 已合并基线、C RealAI/bootstrap 与 D 实际受限执行器已通过组合测试；C 模型端为本地可控 HTTP 替身。正式库/角色部署与生产认证仍未验收，见[验证记录及剩余部署事项](member-d-security-validation.md)。

按用户限定的前三项，现已补齐[独立审计身份/防篡改与角色生命周期](member-d-identities.md)、[严格配置和可直接运行启动入口](member-d-startup.md)。全量后端 645 passed / 37 skipped，真实共享 provider 另行 8 项通过；均为隔离验证。本轮没有浏览器验收、正式库操作、推送或远程 PR 修改。

任务 1 已提供非 Mock `SecurityAdapter`、`build_security` 和 `app.security.main:create_app` 开发入口，见[任务 1 历史接入说明](member-d-task1.md)。该阶段未启用 SQL；当前真实执行能力与可信配置见[执行器说明](member-d-executor.md)。

任务 2 已补齐应用层项目授权：按可信 Context 重新查询当前角色、核对 B 的角色快照、未知动作默认拒绝、撤权与查询故障拒绝授权。权限矩阵、验证及数据库边界见 [授权策略说明](member-d-authorization.md)。

三条正式统计、完整 AST 允许清单、项目参数绑定、双视图、关键审计与[受限执行器](member-d-executor.md)已实现。默认 build_security(data) 保持 allowed/not_executed；可信入口传入 readers 后真实执行，带只读事务、3 秒预算、100 行、256 KiB 及取消清理。真实 SecurityHarness、44+21 攻防和 A+D/A+C+D 本地 HTTP 验证已完成。

## 1. 职责范围

负责最终授权策略、数据库访问策略、自然语言统计的安全网关、SQL 解析/约束/受限执行、关键审计与安全实验。B 只调用 SecurityPort，不执行模型生成 SQL，也不在网关失败后降级查询。MockSecurity 只有固定意图演示，不具备真实 SQL 防护。

## 2. 代码入口与建议位置

- `backend/app/adapters/ports.py:SecurityPort`、`schemas.py:SecureQueryResult/AuditEvent`。
- 参考：`adapters/mock_security.py`；身份和基础项目授权：`auth.py:project_context`；调用：`services.py:secure_query`、`routes/security.py`。
- `backend/app/security/adapter.py` 提供授权、网关和关键审计，`sql_policy.py` 提供封闭 AST，`executor.py` 提供独立受限连接与资源控制；真实共享 provider 为 `security_contracts.postgres_provider:build_harness`。
- 正常/拒绝/故障及审计的可执行合成样例：`contracts/module-port-examples.json`；HTTP 示例沿用 `contracts/examples/` 和 `scenarios/`。

## 3. 完整接口（2 个方法）

```python
def authorize(ctx: Context, action: str, role: str) -> None: ...
async def execute(ctx: Context, question: str) -> SecureQueryResult: ...
```

Context 为 UUID 类型的 user_id/project_id/request_id。B 从开发会话查询 A 的身份/角色，先拒绝非成员，再调用 authorize。现有 action 为 project.read、members.manage、paper.remove、audit.read，role 为 owner/member；后三种要求负责人。role 是可信服务端查询的值，不是前端声明。D 返回 None 表示放行，拒绝用安全 AppError；执行器仍必须把可信项目绑定到数据库访问，不能只依赖前端或角色字符串。

execute 只接收自然语言问题，不接收客户端凭据、Context JSON 或任意 SQL。D 独立拥有 SQL 候选处理、AST/允许清单/项目条件、受限数据库身份、执行资源限制和关键审计；已对齐 A 的 PostgreSQL 16/psycopg3，双视图及项目登录绑定已通过隔离库验证，正式角色生命周期与业务库部署仍需 A/D 联合落实。DataPort.controlled_statistics 只为三个固定意图服务，不是任意 SQL 执行器。真实执行连接/视图在 D/A 模块内注入，不能扩成 B 的通用 SQL API。

## 4. 正常请求/响应示例

来自实际执行 MockSecurity 的 `module-port-examples.json`，不是 SQL 执行证据。

```json
{"ctx":{"user_id":"d9f21e57-118e-5387-8c77-36ab7e27e4cc","project_id":"d4e338d6-16c6-5fa4-b1a8-8b24baf9280f","request_id":"2083434e-6698-535f-9cf1-3f8d783cb166"},"question":"按年份统计论文数量"}
```

```json
{"decision":"allowed","execution_status":"executed","columns":["year","count"],"rows":[{"year":2024,"count":1}],"reason":null,"policy_id":"mock-intents-v1","request_id":"2083434e-6698-535f-9cf1-3f8d783cb166","mock":true}
```

返回固定字段：decision、execution_status、columns、rows、reason(code,message)可空、policy_id、request_id、mock。rows 值只能为 str/int/float/bool/null，不返回驱动对象或 SQL。authorize 的正常调用如 `authorize(ctx,'audit.read','owner')`，返回 None，不返回伪造的认证令牌。

## 5. 拒绝、故障和审计

同样上下文，问题改为“不支持的问题”，实际返回业务拒绝，HTTP 层为200：

```json
{"decision":"rejected","execution_status":"not_executed","columns":[],"rows":[],"reason":{"code":"UNSUPPORTED_QUERY","message":"Mock 仅支持三条文档约定的统计演示问题"},"policy_id":"mock-intents-v1","request_id":"2083434e-6698-535f-9cf1-3f8d783cb166","mock":true}
```

问题“模拟执行失败”返回 undetermined/failed、MOCK_EXECUTION_FAILED reason、无结果；“模拟网关故障”实际抛以下 AppError，HTTP 层补 request_id。真实 D 不需要用特殊中文口令触发故障，应由隔离 fixture 配置执行器/数据库失败。

```json
{"status":503,"code":"GATEWAY_UNAVAILABLE","message":"安全网关不可用，未执行查询"}
```

owner/member 角色不足的实际 Mock 错误为403 OWNER_REQUIRED。与 C/D 共用的异常不能包含连接串、密钥、SQL、未授权标题或内部异常。

约定使用四种常见组合：allowed/executed、allowed/not_executed、rejected/not_executed、undetermined/failed。Schema 实际强制 executed 必须 allowed、非 executed 无 columns/rows、rejected 必须 not_executed、所有未执行状态有 reason；它没有穷举禁止其它非执行组合，不能据此宣称 SQL 策略正确。只有 executed 展示结果，HTTP200或allowed本身不等于执行成功。

审计写入通过 A 的 `append_audit(event:AuditEvent)`，完整字段如下（真实安全事件明确 mock=false；此处为合成例）：

```json
{"event_id":"00000000-0000-0000-0000-00000000000a","timestamp":"2026-10-26T00:00:00Z","request_id":"2083434e-6698-535f-9cf1-3f8d783cb166","actor_id":"d9f21e57-118e-5387-8c77-36ab7e27e4cc","project_id":"d4e338d6-16c6-5fa4-b1a8-8b24baf9280f","action":"contract.gateway","resource_type":"secure_query","decision":"allowed","policy_id":"mock-intents-v1","execution_status":"executed","duration_ms":0.0,"mock":true}
```

```python
# event_dict 为上方 Schema 对应的数据；data 为工厂注入的 DataPort。
data.append_audit(AuditEvent.model_validate(event_dict))
```

event_id 唯一、timestamp 带时区、duration_ms非负。业务请求共用可信 request_id 关联；每次事件用自己的 event_id，不能把 request_id 当事件唯一键。登录审计可空 actor_id/project_id，项目查询只由负责人读取。B 的 requested/finished 是独立业务审计，不能替代 D 执行前/后关键安全事件；关键审计不可用时禁止执行/返回结果。A/D 要联合确定事务/outbox、投递确认、去重和保护。

## 6. 注册、启动及测试

交付 `build_security(data:DataPort, *, readers=None) -> SecurityPort`，readers 是可信项目 UUID 到 ReaderConfig 的映射；用 partial(build_security, readers=...) 注册工厂。代码示例及角色部署要求见[执行器说明](member-d-executor.md)，不在 B 中持有通用 SQL 执行接口。

```python
from app.config import Settings
from app.main import create_app as create_business_app
from app.security import build_security

def create_app():
    # 此示例未配置 readers，保持 not_executed；启用方式见执行器说明。
    # 启动前设置 PAPERPILOT_D_MODE=real，保留统一环境配置。
    return create_business_app(Settings.from_env(), security_factory=build_security)
```

factory 参数已接通；D=real 未注册、构造失败或返回不完整端口明确拒绝，不回退。A/C 可保持 Mock，先实现安全策略和可控执行器测试。工厂形状校验和 real 配置不是安全认证；SDK/数据库执行资源生命周期由 D/组合入口负责并与 B 对齐。生产目前始终拒绝，因为开发身份未替换。

```powershell
# 先运行现成 Mock 骨架；接入 D 后改为其交付的组合入口
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
# 另一终端运行下列测试，无需依赖上述服务
.\.venv\Scripts\python.exe -m pytest backend/tests/security_contracts -q
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
# 先按执行器说明准备 Docker、可选依赖和 A 固定参考路径
.\.runtime\venv312\Scripts\python.exe -m pytest backend/tests/security_contracts --security-contract-provider security_contracts.postgres_provider:build_harness -q
.\.venv\Scripts\python.exe -m pytest backend/tests/test_api.py -q
```

provider 为 `build_harness(*,tmp_path)` context manager，返回 `security_contracts.harness.SecurityHarness`。配置 security、failing_security、unavailable_security、DataPort、合法 owner/member Context、正常/拒绝/故障问题和 is_mock=False。普通/失败/不可达可以是不同配置实例，故障必须来自被测模块的可控依赖，不能用测试外壳伪造安全成功。

8 项共享契约已用真实 provider 通过；独立专用库还完成了 44 攻击 + 21 正常、直接绕过、权限变化、项目隔离、审计故障及资源清理测试，见[验证记录](member-d-security-validation.md)。没有把默认 Mock 契约算作真实攻防。统一启动/前端代理见[开发指南](development-guide.md)。

## 7. 需要交付

SecurityPort 实现和构造入口、策略及标识、SQL 允许/拒绝规则、受限执行器配置、审计调用与失败策略、隔离 provider、安全测试集及实际结果。向 A 交付所需权限/视图/审计保护要求，向 C 明确是否需要 NL2SQL 私有协作，向 B 交付可运行开发组合入口。

## 8. 验收标准

两方法兼容、逐请求可信项目绑定、网关故障无降级、关键审计失败拒绝、结果状态不混淆、错误脱敏。共享契约和 B 集成测试通过之外，必须提供真实测试库的 AST/只读权限/资源限制/跨项目防绕过证据；现有默认3秒/100行目标需由 D/A 落实并记录实测，当前 B 没有实现这些限制。

## 9. 不属于 D

不改前端，不直接改 AB-1/公开 Schema，不替 A 决定数据库或建完整持久化层，不实现论文处理/检索/报告生成，不把任意 SQL 返回 B 执行。生产认证需 A/B/D 共同协调，当前开发身份不可用作真实权限证明。

## 10. 待团队确认

已知基线：A 的 PR #1 已实现 PostgreSQL 16/psycopg3、正式 DataPort、一次性数据库 provider 与 outbox 投递器；B 的 PR #3 已合并；C 的 PR #4 已提供五方法真实 AI 和接受 security_factory 的组合入口。D 已固定 SQLGlot 校验实现。

D 的执行器、结果映射、SecurityHarness、攻防与固定 A/C 本地组合已完成。剩余为正式库安装、视图所有者、项目/审计身份与凭据生命周期、outbox 保留/死信及生产认证；实际合并版本还需最终团队验收。AIPort 没有 SQL 生成方法，C/D NL2SQL 仍为可选项，不能把本地可控模型端当外部模型服务验收。
