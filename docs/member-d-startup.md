# D：可运行的 A+D / A+C+D 组合入口

2026-10-28。`app.security.bootstrap:create_app` 使用 A 正式 DataBinding、独立最小权限审计写身份和每项目读取身份；C 模式决定使用 MockAI 或 C 的真实 bootstrap。不修改 B 公开接口，不自动迁移、创建项目或设置管理员权限，不执行浏览器验收。

## 准备配置

先按[身份与生命周期说明](member-d-identities.md)完成选定库的显式安装、服务身份开通。复制仓库的 security-config.example.json 到 Git 忽略目录 .runtime/security.json，填写实际数据库、已有项目 UUID、已开通角色及其密码环境变量名。配置文件只保存秘密引用，禁止 password 等额外字段。

校验拒绝重复 JSON 键、重复项目/身份/秘密引用、未知字段、超过 64 KiB 的配置、缺失/重复的秘密，以及业务/审计/读取身份混用。配置数据库必须与 A 的 PAPERPILOT_DATA_* 目标一致；每个身份使用不同登录和密码。路径必须显式指定为绝对路径，不从 HTTP、当前目录默认文件或远程地址自动发现配置。文件及环境由操作系统 ACL/本机秘密管理保护，只有可信操作员可修改。

运行环境需要现有 requirements.lock 及 requirements-security-test.lock（当前工作区的 A/C 依赖复现集合）。合并安装时也可按各成员 pyproject 的对应 extras 安装；D 的实际驱动 extra 为 security，不覆盖 A/C 的依赖声明。

```powershell
$env:PAPERPILOT_ENV='development'
$env:PAPERPILOT_A_MODE='persistent'
$env:PAPERPILOT_D_MODE='real'
$env:PAPERPILOT_C_MODE='mock'
$env:PAPERPILOT_SECURITY_CONFIG=(Resolve-Path '.runtime/security.json').Path
# 按 A 的约定显式设置 PAPERPILOT_DATA_HOST/PORT/DBNAME/USER/PASSWORD。
# 审计与每项目密码由配置中的 password_env 指定；秘密由本机安全方式注入。

# A/C 尚未合并时，明确指定已经审阅的本地参考工作树：
.\.runtime\venv312\Scripts\python.exe scripts/run_security.py --a-source .runtime/member-a-step2-reference --port 8000
```

run_security.py 只绑定 127.0.0.1，不自动下载、拉取或修改分支。--a-source/--c-source 是明确授权加载的本地代码目录；操作员须核对来源/提交，不应指向未经审阅代码。当前已验证 A `0b2953c218668af71a4bf869e5c6d238cc0d588d`，C `1f430936acf23c598a91ce794d70ca8c536d39e4`。没有自动把 .runtime 参考加入正常应用搜索路径。

切换 A+C+D 时：

```powershell
$env:PAPERPILOT_C_MODE='real'
# 先按 C AISettings 配置 PAPERPILOT_AI_* 和实际模型/嵌入依赖，不提交凭据。
.\.runtime\venv312\Scripts\python.exe scripts/run_security.py --a-source .runtime/member-a-step2-reference --c-source .runtime/member-c-integration-reference --port 8000
```

实际合并且 A/C 模块已安装后可直接运行：

```powershell
.\.runtime\venv312\Scripts\python.exe -m uvicorn app.security.bootstrap:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

## 启动与资源约定

启动要求 A=persistent/real、D=real；生产模式继续由现有 Settings 拒绝，不绕过开发身份限制。缺 A/C 模块、配置、秘密、合格审计/读取登录或 A readiness 时拒绝启动，不降级 Mock。只有明确 C=mock 才使用 MockAI。

启动检查审计与读取身份的实际 ACL，不执行未经审计的统计查询，不读取候选 SQL。独立审计仍复用 A append_audit，提交前再次检查权限；配置就绪后真实查询经 D 原有执行前/后审计门槛。创建应用失败时清理已创建的 A/审计资源；正常关闭先由 B 结束 worker，再关闭独立审计池和 A 池。

原 `app.security.main:create_app` 及不配置 readers 的 build_security 仍保留校验模式供历史开发，不会被静默启用。新的 bootstrap 是实际组合入口。B 的普通业务日志仍使用业务 DataPort，本项未改业务身份或生产认证；专用审计身份的权限验证不等于防止可信 DBA 或其他管理员连接改库。

test_security_bootstrap_postgres.py 已覆盖 A+D、A+C+D 的实际身份启动及 HTTP 查询、D 审计独立性、配置错误/秘密别名拒绝、连接池关闭和独立启动进程 readiness。C 启动验证使用本地可控模型配置，未验收外部模型效果；本轮不运行浏览器或安装正式业务库。
