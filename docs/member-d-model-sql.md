# C 模型接入 D 的统计查询

默认仍为 `PAPERPILOT_SQL_GENERATOR=template`。显式选择 `model` 后，复用 C 的 `ModelClient` 调用兼容 `/chat/completions` 的服务，模型返回结构化 `{"sql": "..."}` 或 `{"sql": null}`。

模型输出是不可信候选。D 继续校验 AST、从可信模板重建 SQL、绑定服务端项目参数，然后使用已有的独立读取身份执行并记录关键审计。当前仅允许三类已审核统计结构：年份计数、主题计数、未完成证据提取论文。支持这些问题的不同表述，不支持新增筛选条件、连接、任意报表或跨项目查询。

## 配置与启动本机演示

在仓库根目录的 PowerShell 中设置：

```powershell
$env:PAPERPILOT_SQL_GENERATOR = 'model'
$env:PAPERPILOT_SQL_MODEL_BASE_URL = 'https://你的服务地址/v1'
$env:PAPERPILOT_SQL_MODEL_CHAT_MODEL = '服务实际提供的模型ID'

# 交互输入，避免将密钥写进脚本、命令历史和仓库。
$sqlCredential = Get-Credential -UserName 'model-api' -Message '在密码栏输入模型 API Key'
$env:PAPERPILOT_SQL_MODEL_API_KEY = $sqlCredential.GetNetworkCredential().Password
$env:PAPERPILOT_SQL_MODEL_TIMEOUT_SECONDS = '45'
$env:PAPERPILOT_SQL_MODEL_RESPONSE_FORMAT = 'json_schema'
$env:PAPERPILOT_SQL_MODEL_TOKEN_LIMIT_PARAMETER = 'max_tokens'

$demoState = Join-Path (Get-Location).Path ('.runtime/model-sql-demo-' + [guid]::NewGuid().ToString('N') + '.json')
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/preview_integration.py `
  --state "$demoState" --sql-generator model
```

启动 Docker Desktop 后执行。保持终端运行，打开输出 JSON 的 `url`，Alice 进入 `D contract A → 查询与审计`。网站仍使用开发身份登录；研究工作台的 C 模型仍是受控替身，只有统计候选生成调用上面配置的真实模型。预览会自动准备和清理独立临时 PostgreSQL，不连接旧部署或业务库。

这些变量必须在启动后端的同一个终端设置，应用不会自动读取 `.env`。修改模型配置后需要重启对应后端进程。

地址填 API 基础路径；客户端会追加 `/chat/completions`，不要把完整端点再填进去。使用服务实际提供的模型 ID；密钥、模型名或 URL 错误会失败，不会回退为模板成功结果。优先使用服务支持的 `json_schema`；若服务只支持 JSON 对象，可显式设为 `json_object`，本地仍严格校验 JSON 结构。如果服务使用 `max_completion_tokens`，将 TOKEN_LIMIT_PARAMETER 改为对应值。仅统计生成不需要配置向量模型。

停止：在第二个终端执行 `New-Item -ItemType File -Path ($demoState + '.stop')`，其中 `$demoState` 要使用实际完整路径。停止后可清除当前终端密钥：`Remove-Item Env:PAPERPILOT_SQL_MODEL_API_KEY`。

## 复用 C 已有模型

如果没有设置任何 `PAPERPILOT_SQL_MODEL_*` 变量，则读取现有的 `PAPERPILOT_AI_BASE_URL`、`PAPERPILOT_AI_API_KEY`、`PAPERPILOT_AI_CHAT_MODEL` 以及 TIMEOUT_SECONDS、RESPONSE_FORMAT、TOKEN_LIMIT_PARAMETER。只要存在独立 SQL_MODEL 配置，就必须提供完整的 URL、密钥和模型，不会混用两套凭据。支持受保护的 `API_KEY_FILE`，与直接 API_KEY 二选一。

`app.security.bootstrap` 和 `app.dev_composition` 的真实 D 组合入口同样使用这个开关；仅设置环境变量不会给普通 Mock 入口注册真实执行器。已有真实数据库身份和安全配置仍是运行前提。此环境变量开关只允许显式 development/test。HTTPS 生产配置通过独立的受保护 JSON 显式组合模型，不使用该开关，步骤见 [生产部署说明](production-security-deployment.md)。两种入口都保留相同 AST、项目绑定和受限执行校验。

## 检查实际模式

- `/api/v1/health` 的 limitations 包含 `statistics_sql_model`；模板模式为 `statistics_sql_template`。
- 页面说明显示“由模型理解问题并生成候选 SQL”。
- 查询及关键审计的 policy_id 为 `postgres-statistics-v2-model`，与模板、测试注入分开。
- 可尝试“每年分别有多少篇论文？”“按研究方向汇总论文篇数”“哪些论文还没有提取证据？”。
- “删除所有论文”“查询其他项目的论文”等应拒绝；真正的安全保证是 D 对实际候选的 AST 和数据库校验，而非模型是否遵守提示词。

模型不能接触数据库密码、论文正文或真实项目编号；请求只发送当前问题和允许模板。问题会发送至配置的外部服务，应避免在问题中包含秘密。安全允许不代表模型正确理解了业务意图；真实模型的语义准确率仍需使用实际服务单独测量。

数据库执行上限仍为 3 秒，模型请求使用独立的 TIMEOUT_SECONDS。模型超时、认证失败、限流、非法 JSON、字段超长均安全失败并记录失败审计，不会绕过策略或静默降级。

## DeepSeek 配置与故障诊断

使用 DeepSeek 官方 Chat Completions 时，显式配置 JSON 对象模式；当前官方文档的 response_format 只接受 `text`、`json_object`，不接受项目默认的 `json_schema`。`deepseek-flash` 是官方支持的模型 ID。

```powershell
$env:PAPERPILOT_SQL_MODEL_BASE_URL = 'https://api.deepseek.com/v1'
$env:PAPERPILOT_SQL_MODEL_CHAT_MODEL = 'deepseek-flash'
$env:PAPERPILOT_SQL_MODEL_RESPONSE_FORMAT = 'json_object'
$env:PAPERPILOT_SQL_MODEL_TOKEN_LIMIT_PARAMETER = 'max_tokens'
```

在已配置密钥的同一终端，可以只发一次模型请求检查兼容性，不启动数据库、不执行 SQL：

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/check_sql_model.py
```

诊断仅输出基础地址、模型、配置、HTTP 状态和安全错误码，不输出密钥、模型回复或 SQL。`ok: true` / `MODEL_AND_SQL_POLICY_OK` 表示模型输出通过本地安全校验。400 表示参数不兼容，401/403 检查凭据与权限，404 检查地址或模型，429 检查限流或配额；网络失败可能没有 HTTP 状态。错误码 `AI_INVALID_RESPONSE` 表示返回的候选不符合结构，`SQL_POLICY_REJECTED` 表示返回 SQL 不符合当前三类允许结构。

修改配置后必须重启预览，并使用新的状态文件名和新输出的 URL。旧进程不会自动读取终端刚设置的变量。现在模型错误会以 `AI_TIMEOUT`、`AI_PROVIDER_ERROR`、`AI_INVALID_RESPONSE` 或 `AI_UNAVAILABLE` 显示，原始服务响应与凭据不对外暴露。

官方依据（2026-10-31 核对）：[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)；[JSON Output 示例](https://api-docs.deepseek.com/guides/json_mode/)。

## 本地回归

```powershell
.\.runtime\venv312\Scripts\python.exe -m pytest backend/tests/test_model_sql_generation.py backend/tests/test_security_gateway.py -q
```

模型接线回归使用 C 的 HTTP 客户端与本地受控响应，覆盖三类自然语言、结构化输出、恶意候选拒绝、错误配置和失败审计；不冒充真实模型质量验收。
