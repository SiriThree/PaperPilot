# 成员 D 任务 1：SecurityPort 接入

后续任务 2 已补齐当前角色复核与撤权保护，见 [授权策略说明](member-d-authorization.md)。下文任务 1 的 89 项验证保留为当时记录，当前授权行为以任务 2 为准；查询仍全拒绝。

本阶段交付独立的非 Mock 适配器、两方法和构造/启动入口。采用最小实现原则，复用已有注册、错误及结果 Schema，无新增依赖。

## 代码与行为

- `backend/app/security/adapter.py`：`SecurityAdapter`，实现同步 `authorize` 和异步 `execute`。
- `backend/app/security/__init__.py`：导出 `build_security(data: DataPort) -> SecurityPort`。
- `backend/app/security/main.py`：开发组合入口，使用 `security_factory=build_security` 注册。
- `backend/tests/test_security_adapter.py`：适配器、注册、失败关闭与 HTTP 接入测试。

`authorize` 接收服务端可信 Context 和角色：owner/member 可读项目，只有 owner 可以管理成员、移出论文和读取审计；未知动作拒绝，未知角色统一返回资源不可访问。完整策略扩展属于后续任务。

`execute` 使用注入的同一个 DataPort 重新检查成员关系。权限查询异常脱敏为 503 `GATEWAY_UNAVAILABLE`；非成员返回 404 `RESOURCE_NOT_FOUND`。当前所有合法项目的查询均返回：

```json
{
  "decision": "rejected",
  "execution_status": "not_executed",
  "columns": [],
  "rows": [],
  "reason": {
    "code": "QUERY_EXECUTION_DISABLED",
    "message": "安全查询执行链路尚未就绪，未执行查询"
  },
  "policy_id": "security-bootstrap-deny-v1",
  "request_id": "2083434e-6698-535f-9cf1-3f8d783cb166",
  "mock": false
}
```

示例 request_id 实际沿用服务端 Context。这是明确的全拒绝启动策略，HTTP 200 表示收到结构化业务结果，不表示查询成功。`mock=false` 仅标识此结果由 D 的适配器生成，A/C 仍可为 Mock，开发身份和内存审计也仍为 Mock。该适配器不调用 `controlled_statistics`、MockSecurity、模型或 SQL 执行器。

## 启动

在仓库根目录执行，依赖安装参见 README：

```powershell
$env:PAPERPILOT_ENV = 'development'
$env:PAPERPILOT_A_MODE = 'mock'
$env:PAPERPILOT_C_MODE = 'mock'
$env:PAPERPILOT_D_MODE = 'real'
.\.venv\Scripts\python.exe -m uvicorn app.security.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8100 --no-access-log
```

组合入口仍读取统一环境配置，不自动更改模式。D=mock 配合此入口会拒绝启动；D=real 使用原 `app.main:create_app` 而未注册工厂也会拒绝。构造异常由现有工厂脱敏并拒绝启动，无 Mock 回退。适配器当前不创建连接或后台资源，没有额外关闭操作；生产仍被统一配置拒绝。

## 验证命令和阶段边界

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_security_adapter.py backend/tests/test_module_registration.py backend/tests/security_contracts backend/tests/test_api.py -q
.\.venv\Scripts\python.exe -m ruff check backend/app/security backend/tests/test_security_adapter.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
```

共享 `security_contracts` 未指定 provider 时仍测试原 Mock，不能将其结果当成此适配器的真实执行验收。此全拒绝阶段不提供伪造成功的真实 provider。任务 3～8 完成 SQL 候选处理、AST、项目绑定、受限执行器、资源限制、关键审计和隔离 provider 后，才能启用查询并跑真实成功/失败及 40+20 攻防用例。

本阶段不声称真实 SQL 安全网关或生产认证已完成。B 的业务 requested/finished 审计继续保留，D 的执行级关键审计及 A/D 数据库联测仍待后续任务。

## 本次验证记录

2026-10-26，在 Windows、Python 3.14.3 的项目隔离虚拟环境中安装 `requirements.lock`，未修改锁文件。项目要求 Python >=3.12；文档基线的 3.12 本轮未复验。

- 上述适配器、模块注册、默认 Mock 共享契约及 API 测试组合：89 passed。包含 D 适配器通过真实注册入口的 HTTP 拒绝路径；默认共享测试仍只证明 Mock 结构行为。
- 新增代码 Ruff 静态与格式检查通过，OpenAPI 和 C/D Mock 示例一致性检查通过。
- 测试存在已有的 Starlette/httpx TestClient 弃用提示，不影响结果。
- 未启动外部服务、连接真实数据库或执行 SQL；未运行前端、真实 SQL 攻防及生产验收。
