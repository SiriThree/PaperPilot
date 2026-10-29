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



按下面的顺序展示，可以把 **D 的页面 P0、截至 10 月 29 日的安全能力，以及实验指标**逐项对应起来。

先启动 Demo，打开启动脚本输出的 `url`。默认用 **Alice** 登录，进入 **D contract A → 查询与审计**。完整启动方法见本文“启动与停止”。

**一、在网页逐项展示**

| 序号  | 验收功能         | 具体操作                                                          | 通过时应看到什么                                               |
| --- | ------------ | ------------------------------------------------------------- | ------------------------------------------------------ |
| 1   | 当前身份与权限状态    | Alice 进入 A 的“查询与审计”，查看页面顶部                                    | 当前身份、负责人角色、项目名称、允许的操作清晰显示。                             |
| 2   | 模板降级明确标识     | 查看查询区域上方说明                                                    | 明确说明当前使用三类固定模板，不调用模型生成 SQL。                            |
| 3   | 按年份统计        | 点击“按年份统计论文数量”，再点“提交查询”                                        | `allowed / executed`，新 Demo 的 A 返回 2026 年论文计数，并展示请求编号。 |
| 4   | 按主题统计        | 切换到 D contract B，提交“按研究主题统计论文数量”                              | 返回 B 的 `security` 主题统计。A 没有主题数据时返回空结果也属于正常情况。          |
| 5   | 查询未完成证据提取的论文 | 回到 A，提交“查询尚未完成证据提取的论文”                                        | 初始状态能查到新导入的“受控 PDF 联调样例”。                              |
| 6   | 查询反映真实业务数据   | 到研究工作台的“证据”页签，执行“提取证据”；完成后重复第 5 项                             | 该论文从未提取列表消失，证明查询读取了实际数据库状态。                            |
| 7   | 不支持的问题安全拒绝   | 在统计问题框输入“不支持的问题”，提交                                           | 显示拒绝原因，状态为 `rejected / not_executed`，没有结果行。            |
| 8   | 项目之间的数据隔离    | 分别查询 A、B 的年份统计                                                | 新 Demo 中 A 为 2026，B 为 2025，各自结果不混入另一项目的数据。             |
| 9   | 审计记录可视化      | Alice 在查询后查看下方“项目审计”                                          | 展示操作者、项目、时间、事件、决策、执行状态及请求编号。                           |
| 10  | 成功与拒绝均有审计    | 分别完成一次合法查询和第 7 项拒绝查询；事件类型填 `security.query.finished`，点击“筛选审计” | 能找到两类完成事件，决策分别为允许和拒绝。                                  |
| 11  | 审计条件筛选       | 分别设置事件类型、决策、用户 UUID、起止时间，再点击“筛选审计”                            | 结果符合筛选条件。时间输入需带时区，例如 `2026-10-29T00:00:00+08:00`。      |
| 12  | 查询与审计关联      | 复制查询结果中的请求编号，与审计表中的编号比较                                       | 能定位同一次查询的审计记录。当前页面通过查看编号关联，不是请求编号搜索框。                  |
| 13  | 普通成员权限       | Alice 在项目总览“管理成员”中将 Bob 加入 A；退出后用 Bob 登录 A                    | Bob 显示成员角色，可以统计查询；不能管理成员、移出论文或查看完整项目审计。                |
| 14  | 非成员越权拒绝      | Alice 复制 A 查询页地址；退出后用 Eve 登录，再打开该地址                           | 无法取得 A 的统计或审计；服务端返回 `RESOURCE_NOT_FOUND / 404`。        |
| 15  | 即时撤销权限       | 普通窗口使用 Alice，无痕窗口使用 Bob；Alice 从 A 移除 Bob，Bob 再提交查询            | Bob 的后续请求被拒绝，旧页面或旧身份状态不能继续授予权限。                        |

第 13、14 项可再用浏览器开发者工具查看 Network：**Bob 读取完整审计应为 403，Eve 访问 A 应为 404**，这样能证明服务端确实执行了权限校验。

**二、用隔离测试展示底层安全功能**

这些功能需要真实攻击候选、数据库权限或故障条件。网页输入一段 `DELETE` 后被拒绝，只能证明问题范围限制，不能证明 AST 防护。

在仓库根目录运行：

```
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-security.xml `
  backend/tests/test_security_authorization.py `
  backend/tests/test_security_executor_postgres.py `
  backend/tests/test_security_identities_postgres.py `
  backend/tests/test_restricted_executor_postgres.py
```

|序号|验收功能|具体展示操作|通过时应证明什么|
|---|---|---|---|
|16|可信身份与项目绑定|展示授权测试及项目参数篡改测试结果|请求中伪造角色、替换项目编号，不能扩大访问范围。|
|17|AST 与 SQL 允许清单|展示真实链路攻击测试、正常 AST 变体测试结果|恶意候选被拒绝；允许的三类统计及合法变体能够执行。|
|18|强制经过安全检查|展示直接调用执行器的绕过测试结果|绕过上层网关调用执行器，也不能执行未经验证的候选。|
|19|数据库第二层隔离|展示错误项目登录、底表访问和写操作测试结果|项目读取身份不能获取其他项目数据，不能任意读底表或写入。|
|20|只读执行|展示只读事务与服务端限制测试结果|统计运行在只读事务中，写操作受到数据库约束。|
|21|3 秒执行限制|展示慢查询及锁等待测试结果|查询超时后安全失败，不返回结果，并清理连接。|
|22|最多 100 行|展示行数上限测试结果|返回结果不超过 100 行。|
|23|完整响应最多 256 KiB|展示响应超限及多字节内容测试结果|超限时返回明确失败，不返回部分数据。|
|24|取消及断连处理|展示取消清理、数据库断连测试结果|不遗留执行会话；后续查询可使用新连接。|
|25|独立审计、禁止篡改|展示审计身份及权限漂移测试结果|审计身份不能读取业务正文、修改或删除日志；异常授权会被检测。|
|26|审计故障时安全失败|展示关键审计故障测试结果|执行前审计失败会阻止执行；执行后关键审计失败不会把查询结果当作成功返回。|

这些测试使用独立临时数据库，不需要破坏正在演示的网页环境。

**三、单独展示认证、提示词注入与实验指标**

| 序号  | 验收功能               | 具体操作                 | 通过时应看到什么                                                 |
| --- | ------------------ | -------------------- | -------------------------------------------------------- |
| 27  | 密码登录、会话与 CSRF      | 运行下面的命令 A            | 凭据登录、CSRF、会话重启、受限查询及审计联合用例通过。网页的 Alice/Bob 快速切换属于开发身份演示。 |
| 28  | 用户及论文提示词注入边界       | 运行命令 B               | 5 项恶意用户输入、5 项恶意论文内容测试通过；恶意模型输出不能扩大引用或数据库访问范围。            |
| 29  | 三组防护对照             | 运行命令 C，打开输出 JSON     | 同时展示无网关、仅网关、网关加数据库权限的攻击结果、正常通过率及数据库绕过结果。                 |
| 30  | 网关新增开销 P95 ≤200 ms | 查看命令 C 输出中的各类计时和原始样本 | 三类查询分别满足指标，并说明硬件、数据规模、样本量和计时口径。                          |

命令 A：

```
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-credentials.xml `
  backend/tests/test_scoped_audit_postgres.py::test_credentials_scoped_audit_and_real_security_browser
```

命令 B：

```
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_integration.py `
  --report .runtime/d-demo-prompts.xml `
  backend/tests/test_prompt_injection_postgres.py
```

命令 C：

```
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/evaluate_security.py `
  --output .runtime/d-demo-evaluation.json
```

已有归档实验结果为：三组正常通过率均为 100%；受控攻击候选执行率依次为 100%、0%、0%；三类查询新增开销 P95 约为 **70.7、72.7、76.1 ms**。现场复跑应以本次输出为准。

最后展示两份文档，补齐截至 10 月 29 日的设计与验收证据：

- [威胁模型与权限矩阵](member-d-threat-model.md)：对应信任边界、角色权限及风险分析。
- [D 本地验收清单](member-d-acceptance-20261029.md)：对应日期要求、页面 P0、测试结果与交付边界。

上述为验收操作对应表；后续审计详情改动的验证结果见[审计详情说明](member-d-audit-details.md)。当前 Demo 使用固定统计模板和受控模型替身，不能据此验收真实模型质量或最终团队部署。
