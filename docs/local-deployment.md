# 本机持久运行与 B 方案对齐

本机入口 `https://localhost:8443`，PostgreSQL `127.0.0.1:54327`，只监听回环地址。独立持久卷为 `paperpilot-local_postgres`；不要使用 `down -v` 删除它。账号 `owner`，本机随机密码保存在 `.runtime/local-deployment/secrets/owner-password`，不在聊天、PR、截图或日志中展示。

当前统一采用 B 的 `credentials`、Argon2、`public.users` 凭据与 `public.auth_sessions`；登录、退出完成审计与会话操作同一事务。CSRF 采用可信 Origin、Cookie 和请求头配对；HTTPS Cookie 为 Secure、HttpOnly、SameSite=Strict。B 的独立认证边界与 NOLOGIN 凭据行锁所有者用于隔离认证连接；业务身份不能读取密码摘要、访问会话或改写凭据。

本机配置明确为 `ENV=test`，启用 B 的独立认证候选，并保留 D 原生受限 SQL 与独立审计写身份。它是持久的本机验收环境，**不是生产环境**；B 的生产启动拒绝门槛保留。没有把 005—015 的候选业务/审计 RLS 自动安装到现有业务库。统计仍是 SESSION_USER 安全屏障视图，不宣称已改造全部底表 RLS。AI 默认 C=mock；固定三类统计模板与真实模型生成 SQL 分开，不冒充模型效果。

## 已有 D 环境升级

升级只接受本仓库已登记的旧环境：配置 format=1、A schema 3、D scrypt 账户表。先在旧版本运行备份并确认恢复演练，再停止网站、更新代码和依赖，显式执行：

```powershell
.\.runtime\venv312\Scripts\python.exe -m pip install -r requirements.lock
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py upgrade
```

要求一小时内已校验备份。升级拒绝未知账户或不匹配的本机 owner 密码，并在修改前核对；当前自动路径只支持原初始化的唯一 owner，额外账户须先安排逐个凭据迁移。升级使用 B 的 007 汇合迁移保留旧 002/003 校验值，不播种/覆盖项目、论文、任务或报告。owner 转为 B Argon2，记录迁移审计并使旧会话失效，保留旧只读历史表；重新供应 business/auth 登录，旧登录 NOLOGIN 并终止连接，审计与项目 reader 保持独立。

部分步骤失败时保持停服，保留备份和现场，不自动删除/重建数据库，也不盲目重试。格式为 2 的已升级环境拒绝重复升级。升级属于本机明确维护流程，不能对未知服务器或生产库执行。

## 新机器初始化与日常启停

需要 Docker Desktop、Python 3.12、Node.js 24、OpenSSL；安装主 lock、PostgreSQL 可选依赖后：

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py init
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py start
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py status
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py restart
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py stop
```

init 拒绝已有目录、已有 Docker 项目/volume 或占用端口，不接管其他资源。管理员显式安装 A 的迁移 1—7、D 统计视图/生命周期与 B 凭据锁；应用启动不执行 DDL。Windows 凭据文件使用当前用户所有权和受保护的专用 DACL，POSIX 文件要求 0600；复用 B 的实际权限检查，拒绝符号链接和不安全文件，不只检查路径。应用环境只包含文件引用，不含管理员口令。

本机证书为自签名，浏览器会提示不受信任。先核对 localhost 地址与证书；脚本没有自动修改系统信任。stop 只关闭网站，数据库和 volume 保留。Start 必要时以隐藏窗口启动 Docker Desktop。密码重置/禁用使用 `manage_accounts.py` 管理员流程，变更与审计同事务，不提供公开注册。新项目统计身份仍须显式 `reader-add --project <UUID>` 后重启，不授予应用 CREATEROLE。

## 备份 恢复及巡检

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py backup
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py restore-drill --backup <备份绝对目录>
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py monitor
.\scripts\install-local-tasks.ps1
```

备份在维护窗口停止网站和 worker，保存数据库、全部角色、uploads、配置、TLS 与服务凭据，比较导出前后表/文件指纹；操作互斥。HMAC 密钥 `secrets/backup-integrity` 单独保管，不包含在备份中。恢复在独立带标签容器进行，比较全部表、文件及迁移校验和，重绑服务角色 OID，清空新旧会话表，复验 B 认证边界和 D 审计/读取身份。它不覆盖当前业务库。升级前备份仅用于保全旧版本；B 恢复验收须使用升级后新备份与新配置。

当前用户计划任务：登录启动、03:00 备份、每 15 分钟巡检、03:30 认证元数据清理。只在用户登录且电脑运行时工作。巡检网站/数据库、死信、过期任务与 B 投递租约、24 小时备份新鲜度、5 GiB 磁盘阈值及 14 天证书阈值，写 operations-status.json，异常退出 1；不向外部联系人发送消息。

仅清理过期或撤销的 B 会话、超过 24 小时的限流键。业务、审计、outbox、幂等与重投历史保留；B 未知投递结果必须对账后显式重投，不把旧的 delivery_lease_until 或 claim_token 当作当前投递依据。备份仍是同机受 ACL 保护副本，未实现离机加密/密钥托管，不代表完整灾难恢复或有人值守。

## 验收与模型

`verify_local_deployment.py` 验证真实 HTTPS、CSRF、B 密码会话、D 统计与关键审计、重启后会话和退出。`frontend/playwright.local.config.ts` 用于本机浏览器，关闭截图/trace/video。`export_real_security_evidence.py` 运行统一代码的隔离 PostgreSQL 套件，显式启用 B 隔离数据库组；外部模型启动测试仍需额外授权配置，不能把跳过计作通过。

模型接入仍使用 `model-configure --file <绝对 JSON 配置>`，只接收 PAPERPILOT_AI_* 字符串，并在重启时显式加载真实 C。需提供服务地址、模型名、密钥文件、embedding 服务或权重目录、维度与不可变 revision；配置通过不等于模型验收。现有备份不包含外部服务账户或模型权重。PRD 三组安全对照、网关 P95 和 C/D 提示词注入回归仍须单独交付。
