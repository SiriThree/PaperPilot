# 成员 D 任务 2：项目授权策略

`SecurityAdapter.authorize(ctx, action, role)` 继续保持 SecurityPort 签名，正常返回 None，拒绝抛出脱敏 AppError。复用同一个 A DataPort 查询最新项目角色，不保存角色缓存，不创建独立用户或权限来源。

## 权限矩阵

| 操作 | owner | member |
|---|---|---|
| project.read | 允许 | 允许 |
| members.manage | 允许 | 403 OWNER_REQUIRED |
| paper.remove | 允许 | 403 OWNER_REQUIRED |
| audit.read | 允许 | 403 OWNER_REQUIRED |
| 任何未知操作 | 403 ACTION_FORBIDDEN | 403 ACTION_FORBIDDEN |

动作名精确匹配，不支持通配符、大小写变体或隐式默认权限。非成员不进入上表判断，统一按资源不可访问处理。

## 可信上下文与当前角色

1. B 从已认证的服务端会话构造 Context，并从 A 查询角色；浏览器不能提供 Context 或角色声明。
2. D 要求 Context 是内部 dataclass，且 user_id、project_id、request_id 都是 UUID 对象。JSON、字符串编号或缺失上下文不会被自动转换为可信对象。
3. D 按 `ctx.user_id + ctx.project_id` 再次查询 A 的当前角色。非成员、不存在的用户/项目或无效角色均返回 404 `RESOURCE_NOT_FOUND`，不暴露资源是否存在。
4. 传入角色必须为 owner/member，且与本次重新查询的当前角色一致。角色快照不一致返回 403 `ACTION_FORBIDDEN`，要求重新发起请求；不能沿用撤销前的负责人权限。
5. A 的角色查询发生任何普通异常（包括可能带内部详情的 AppError）时，D 转为固定 503 `GATEWAY_UNAVAILABLE`，不回退 Mock、不使用缓存角色。

角色针对当前项目：同一用户在项目 A 是 owner，不代表在项目 B 也有负责人权限。适配器复用时每次重新查询，移除成员后后续调用立即失效。

Context 的类型检查只验证输入结构，不能证明来源或真实身份；身份可信性仍依赖 B 的认证和可信组合入口。当前仍使用开发会话，生产认证尚未交付。request_id 只用于关联，不作为权限凭据。

## 查询入口和事务边界

`execute` 与 `authorize` 复用 `_project_role`，确保重新校验的用户与项目来自同一 Context；它仍返回 `rejected/not_executed` 和 `QUERY_EXECUTION_DISABLED`，不执行 SQL。其 `policy_id=security-bootstrap-deny-v1` 标识当前查询全拒绝策略，不表示 SQL 防护已实现。

D 的复核不能锁住检查后的权限状态。A 仍须在每次实际资源读写中复核项目与角色，事务内约束、受限数据库身份/RLS、连接池上下文复位和未来执行器的项目参数绑定仍按 AB-1 与 A/D 联合落实。此次未新增通用 SQL 入口，也未修改 B 的预检逻辑、DataPort、公开 Schema 或前端。

## 验证与提交检查

2026-10-26，Windows、Python 3.14.3，复用项目隔离虚拟环境与锁定依赖。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_security_authorization.py backend/tests/test_security_adapter.py backend/tests/test_module_registration.py backend/tests/security_contracts backend/tests/test_api.py -q
.\.venv\Scripts\python.exe -m ruff check backend/app/security backend/tests/test_security_adapter.py backend/tests/test_security_authorization.py
.\.venv\Scripts\python.exe -m ruff format --check backend/app/security backend/tests/test_security_adapter.py backend/tests/test_security_authorization.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
git diff --check
```

- 组合测试 144 passed；其中新增授权回归 55 项。测试使用隔离 MockData 和 D 的实际适配器，没有访问运行数据库。
- 覆盖跨项目/未知用户/未知项目、撤权、角色快照变化、同一用户多项目不同角色、上下文结构、异常脱敏及精确动作允许清单。
- HTTP 验证覆盖负责人/成员权限与移除成员，并模拟 B 预检到 D 复核之间的撤权、降权和数据故障，验证安全错误和 request_id。
- 原测试中以 Alice 上下文假设 member 的夹具已改为实际 Bob 上下文，确保角色与 A 的成员关系一致。
- 静态与格式检查、公开契约/Mock 示例一致性及差异空白检查通过；无新增依赖和公共接口变更，应用层授权改动具备提交条件。

默认 `security_contracts` 仍验证 Mock 结构行为，不能据此宣称真实 SQL、受限身份/RLS、关键审计或 40+20 攻防验收通过。保留已有 Starlette/httpx TestClient 弃用提示；Python 3.12、前端和正式数据库本轮未复验。
