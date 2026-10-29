# 成员 D 演示操作手册

建议采用“网页功能与权限 → 原始实验报告 → 隔离故障测试”的顺序。网页使用真实 PostgreSQL、D 的 AST 网关、项目独立读取身份和独立审计写入身份；登录为显式开发身份，三类统计为固定模板，C 的模型端为本地受控替身。演示开始时说明这些边界，不将模板或替身称为真实模型生成 SQL／质量验收。

## 启动与停止

本机已有 Python 3.12 环境、前端依赖及固定 Docker 镜像。先启动 Docker Desktop，在 PowerShell 进入仓库根目录：

```powershell
Set-Location 'D:\Study\LZZ的知识库\大三上\数据库原理及安全\大作业\PaperPilot'
$demoState = Join-Path (Get-Location).Path ('.runtime/d-demo-' + [guid]::NewGuid().ToString('N') + '.json')
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/preview_integration.py --state "$demoState"
```

保持这个终端运行。脚本输出 JSON 后，打开其中的 `url`，不要固定输入 8443、8000 或 5173；每次使用独立端口、项目 UUID 和数据库。浏览器地址应为 `http://127.0.0.1:<本次端口>`。

第二个终端中，可读取同一个状态文件：

```powershell
# 将下方路径替换为第一个终端输出的完整状态文件路径。
$demoState = 'D:\...\.runtime\d-demo-本次编号.json'
$demo = Get-Content -Raw -Encoding UTF8 -LiteralPath $demoState | ConvertFrom-Json
$demo.url
$demo.project_id
$demo.stop_file
```

如果由助手预先启动了 Demo，可直接读取指针文件获取本次路径：

```powershell
$launch = Get-Content -Raw -Encoding UTF8 .runtime/d-demo-current.json | ConvertFrom-Json
$demoState = $launch.state_file
$demo = Get-Content -Raw -Encoding UTF8 -LiteralPath $demoState | ConvertFrom-Json
$demo.url
```

停止时创建状态中指明的停止标记，等待启动终端退出：

```powershell
New-Item -ItemType File -Path $demo.stop_file -Force | Out-Null
```

停止会清理本次临时数据库和服务。下次启动使用新的状态文件；演示中新增的数据不承诺在停止后保留。它不会恢复或覆盖以前归档的本机部署，不需要删除任何持久卷。

## 网页演示顺序

| 步骤  | 操作                                                                  | 应展示的 D 能力与预期结果                                                                               |
| --- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 1   | 登录页选择 alice，点击“进入演示”，进入 D contract A → 查询与审计                        | 显示开发身份、负责人、当前项目与可执行操作；页面明确三类固定模板降级。                                                          |
| 2   | 提交“按年份统计论文数量”                                                       | allowed / executed，真实返回 2026 年的演示论文计数，展示请求编号。不是 Mock 执行。                                     |
| 3   | 提交“查询尚未完成证据提取的论文”                                                   | 新导入的“受控 PDF 联调样例”在未提取列表内。                                                                    |
| 4   | 进入研究工作台，切到“证据”页签，点击“提取证据”；任务完成后回查询页重复步骤 3                           | 该论文从未提取列表消失，说明统计反映实际数据库状态。提取结果仍待人工核验，不代表 AI 正确率。                                             |
| 5   | 切换 D contract B，提交年份和主题统计                                           | B 使用其独立数据和项目读取身份：预置年份为 2025，主题包含 security；A 不会混入 B 的统计。A 新论文未配置主题时，主题查询允许返回空结果。              |
| 6   | 回 A，选择“不支持的问题”并提交                                                   | rejected / not_executed、明确原因、无结果行。此步骤展示范围拒绝；真正的 AST 攻击由后面的候选注入测试证明。                          |
| 7   | 审计事件类型填 security.query.finished，选择 allowed 或 rejected，并填写时间、用户 UUID | 可以追踪允许和拒绝事件；请求编号关联对应查询。日期必须带时区，如 2020-01-01T00:00:00Z 到 2099-01-01T00:00:00Z；演示结束前可改为实际时间范围。 |
| 8   | Alice 回项目总览，选择 A 的“管理成员”，添加 bob；退出后用 bob 进入 A                       | 显示成员及可执行操作；不能管理成员、移出论文或读取完整项目审计，仍可执行当前项目统计。                                                  |
| 9   | 复制 A 的查询页地址，退出后用 eve 登录，再打开原地址                                      | RESOURCE_NOT_FOUND、权限待确认，无法取得 A 的统计或审计。账号选择只是此演示环境的身份切换。                                     |
| 10  | 普通窗口保持 Alice，无痕窗口登录 Bob；Alice 从 A 移除 Bob，Bob 刷新／再发请求                | 后续请求立即失去项目权限；不要使用同一浏览器普通窗口模拟两个人，因为普通窗口通常共享 Cookie。                                           |

审计中 `secure_query.*` 是业务请求事件，`security.query.*` 是 D 的执行级事件；同一次请求通过 request_id 关联。拒绝请求可能只有完成事件，没有执行前 prepared 事件。

若要在浏览器开发者工具中证明“服务端同样拒绝”，Bob 登录后调用下面的 GET，成员应为 403，Eve 应为 404。使用 Alice 复制的真实项目 UUID：

```javascript
fetch('/api/v1/projects/替换为A项目UUID/audit-events')
  .then(async r => console.log(r.status, await r.json()))
```

在 Eve 身份下还可直接验证统计请求：

```javascript
fetch('/api/v1/projects/替换为A项目UUID/secure-queries', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-PaperPilot-Client': 'web' },
  body: JSON.stringify({ question: '按年份统计论文数量' })
}).then(async r => console.log(r.status, await r.json()))
```

应返回 404 且没有 rows。不要复制或展示 Cookie、密码、环境变量和凭据配置。

## 需要用隔离测试展示的功能

网页不提供原始 SQL 输入接口。在网页输入 DELETE 或 SELECT 只会先被自然语言范围路由拒绝，不能据此宣称验证了 AST。使用以下实际候选注入、受限数据库和故障测试：

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-security.xml `
  backend/tests/test_security_executor_postgres.py `
  backend/tests/test_security_identities_postgres.py
```

当前两组共 178 项，覆盖 44 个攻击、44 个直接执行器绕过、21 个正常变体，以及只读、3 秒超时、100 行、256 KiB、多字节响应、取消清理、独立审计、幂等、角色与函数定义漂移和失败关闭。这些用例有重叠作用，不能把分组数字另行相加为独立攻击数量。它们使用自己新建的测试库，故障不会施加到正在展示的网页 Demo。

凭据登录、CSRF、会话重启和受限审计的完整联合验证，可另外运行：

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-credentials.xml `
  backend/tests/test_scoped_audit_postgres.py::test_credentials_scoped_audit_and_real_security_browser
```

该用例自己供应临时凭据并运行浏览器，避免把开发身份切换当作密码认证验收。不要手工停用网页的审计账号或给网页数据库加锁来演示故障。

## 三组实验与提示词注入

可先展示 `docs/security-evaluation/results.json` 和本次 [D 验收清单](member-d-acceptance-20261029.md)，再按需现场复跑：

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/evaluate_security.py `
  --output .runtime/d-demo-evaluation.json
```

输出三组攻击／正常通过率和网关 P95。40 项固定攻击分四类，每类 10 项；21 项正常查询与 A 的独立统计结果比较。另有数据库绕过探测，说明仅应用网关无法阻止绕过应用后的直接访问，完整配置的视图过滤及原生 ACL 能继续限制范围、底表和写操作。基线中的写操作在自己的隔离库逐次回滚。

现场重跑写入 `.runtime`，不会覆盖已经归档的基线报告。引用最新结果时同时展示数据规模、样本量和计时口径，不只截取一个低耗时。

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-prompts.xml `
  backend/tests/test_prompt_injection_postgres.py
```

十项回归包含五项恶意用户输入和五项恶意论文正文；使用真实解析和故意服从攻击者的模型响应，证明引用／项目边界仍拒绝。它不测量真实模型拒绝恶意指令的概率。

## 自动演示

Demo 保持运行，在第二个 PowerShell 中设置本次 URL，再运行现有真实浏览器用例。加 `--headed` 会打开可见浏览器：

```powershell
$env:PAPERPILOT_INTEGRATION_URL = $demo.url
Set-Location frontend
npx.cmd playwright test --config playwright.integration.config.ts `
  security-demonstration.spec.ts --headed --reporter=line
```

该自动演示会真实提交查询、增加 Bob 成员关系并产生审计。需要重新展示“尚未提取”的初始状态时，停止后重新启动一个新的 Demo；不要手改业务数据伪造状态。

答辩可用的准确表述是：本机演示验证了 D 的授权、AST、受限执行和审计链；真实外部模型质量、最终团队数据和干净环境交付另行共验。
