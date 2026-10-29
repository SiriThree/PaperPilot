# 本机持久运行与运维

本轮目标是当前 Windows 用户的本机环境。网站只监听 `https://localhost:8443`，PostgreSQL 只监听 `127.0.0.1:54327`，没有发布到局域网或公网。持久数据库采用独立 Docker volume `paperpilot-local_postgres`，不复用 day0 或隔离测试库。请勿执行 `docker compose ... down -v`，它会删除数据库。

当前组合为 **A=persistent、C=mock、D=real、auth=password**。AI 的服务地址、模型名、密钥文件或权重目录尚未提供，因此不能称作真实模型验收或完整生产部署。模型替身的报告不能代替真实模型效果、引用与性能验收。

## 已有环境怎样使用

在仓库根目录执行：

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py start
```

打开 `https://localhost:8443`。账户名为 `owner`，随机初始密码在 `.runtime/local-deployment/secrets/owner-password`。用本机编辑器读取，不要粘贴到聊天、PR 或截图中。初始项目为“本机持久项目”。数据库账号和网站用户是两套不同身份。

本机使用自签名 TLS 证书，浏览器会提示证书不受信任；先核对地址确实是本机，再查看证书，或自行将 `tls/localhost.crt` 加入当前用户信任。脚本**没有**自动修改系统证书信任。公开服务需另配可信证书与域名。

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py status
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py restart
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py stop
```

`stop` 只停止网站，数据库仍保留并运行。普通关闭 Docker Desktop不会删除 volume；重新启动 Docker 后可再次 `start`。启动脚本会在必要时以隐藏窗口启动已安装的 Docker Desktop，并等待就绪。网站启动失败会拒绝服务，不会回退到演示数据。

## 新机器首次初始化

需要 Docker Desktop、Python 3.12、Node.js 24、OpenSSL，安装现有 lock 依赖及 `requirements-security-test.lock`。仓库已有安装命令见 README。先构建前端，再初始化：

```powershell
npm.cmd --prefix frontend run build
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py init
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py start
```

`init` 只接受全新目录、空闲端口和未使用的 Docker 项目/volume，拒绝接管已有资源。初始化包含 A 的迁移 1—3、D 安全视图/账号登记、独立认证 schema、网站 owner 及初始项目。部分初始化失败时保留现场，先检查日志和资源；不要为了重试直接删除已有目录或 volume。

创建 business、audit、auth、project reader 四种独立最小权限数据库登录，密码互不相同。读取登录只读两个受项目约束的视图；auth 登录只能校验凭据和管理会话，不能改密、创建用户或读取论文正文。审计失败时不发放登录会话，安全查询仍执行原有审计前后门槛。隔离机制是受保护视图与登录绑定，**不是 A 底表原生 RLS**。

密码采用独立随机盐的 scrypt（N=2^17、r=8、p=1），参数参考 [OWASP Password Storage Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Password_Storage_Cheat_Sheet.md)。数据库只保存密码哈希和会话令牌 SHA-256；Cookie 设置 Secure、HttpOnly、SameSite=Strict。会话最多 8 小时、闲置最多 30 分钟，改密或禁用账户立即使已有会话失效。登录限流按账户与来源地址保存在数据库中。

`.runtime/local-deployment` 仅授权当前 Windows 用户访问，Git 忽略该目录。应用环境只引用 `*_FILE`，不含管理员密码；日志不打印凭据、候选 SQL、会话令牌或原始异常。同一 Windows 用户仍能读取管理员文件，因此这是本机管理员信任边界；公网部署还应使用独立服务 OS 身份和专门秘密管理。

## 账户与项目管理

不提供公开注册或“演示账户升级”。管理员使用 `scripts/manage_accounts.py create/password/disable`；密码仅通过命名环境变量或绝对 `*_FILE` 读取，不作为 CLI 参数。该工具的 `PAPERPILOT_DATA_*` 必须指向受信任管理员身份，网站业务身份没有账户开通/改密权限。账户变更与审计写入在同一事务提交。

新建项目后，管理员显式为该项目分配数据库 reader，再重启网站加载绑定：

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py reader-add --project <项目UUID>
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py restart
```

没有绑定的新项目仍可进行普通业务操作，统计网关拒绝执行。应用没有 CREATEROLE 权限，不会自行创建/共享项目读取身份。账号数据库层轮换与撤销仍使用 `manage_security.py`；当前 secret 文件和配置也需同步更新，避免旧配置导致启动拒绝。

## 接入已有授权模型

提供本机模型配置 JSON 的绝对路径，其中只包含 `PAPERPILOT_AI_*` 字符串配置，密钥推荐 `PAPERPILOT_AI_API_KEY_FILE`。需要聊天服务 URL、模型名、embedding 服务或已有权重路径、维度，以及持久索引要求的不可变 revision。详见 [C 配置](ai-adapter.md) 和 [持久索引](persistent-index.md)。不要在聊天中发送密钥值。

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py model-configure --file <绝对配置路径>
.\.runtime\venv312\Scripts\python.exe scripts/local_deployment.py restart
```

该命令校验配置完整性后显式切换 C=real，实际调用失败不会降级。它不证明模型有效。随后需以公开授权 PDF 执行 `scripts/evaluate_ai.py --live-models`，人工复核引用页码和文本，并在本机 A+C+D 路径验证上传、检索、回答、证据、报告与性能。当前还未执行此步。现有备份不包含外部模型服务账户或模型权重，须另行保管配置/密钥和可复现的权重 revision。

## 备份与恢复

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py backup
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py restore-drill --backup <输出的备份绝对目录>
```

备份先进入维护窗口，停止网站和 worker，导出数据库、角色、uploads、凭据、TLS 和配置，再恢复此前运行状态。校验全部表和上传文件在导出期间没有变化。CLI 操作互斥，避免计划任务与手动启停冲突。备份有独立 HMAC 完整性校验，密钥 `secrets/backup-integrity` 不放进备份；请单独妥善保管，否则无法验证恢复包。

恢复演练在新建、带所有权标签的临时 PostgreSQL 容器执行，保留角色/ACL，重新绑定角色 OID，清空恢复库会话，并比较全部表的行数/内容指纹、文件哈希、迁移校验和，复验 auth、audit 和 reader 隔离。临时容器完成后清理。它**不会覆盖现有业务库**。报告为 `last-restore-drill.json`。真正灾难切换需先保全故障现场，再由管理员选择已验证副本，不自动覆盖仍有数据的库。

当前备份是同机、ACL 保护的恢复副本，含数据库角色及密码哈希、服务凭据、私钥，**没有做离机加密备份或灾难切换**。不要上传或发送该目录。正式使用还需选择 BitLocker/加密介质、异机副本、密钥托管和恢复责任人；同一块磁盘损坏仍可能同时丢失库与备份。默认保留所有备份、业务、审计、outbox 与幂等记录；未实现自动删除业务历史，也没有把这个默认值当作团队批准的数据保留期限。

## 定时任务与告警

```powershell
.\scripts\install-local-tasks.ps1
# 移除本仓库登记的四项任务：
.\scripts\install-local-tasks.ps1 -Remove
.\.runtime\venv312\Scripts\python.exe scripts/local_operations.py monitor
```

四项当前用户任务：登录 Windows 时启动网站、每天 03:00 备份、每 15 分钟巡检、03:30 清理认证过期元数据。只在当前用户已登录且电脑运行时工作，不是无人值守服务器服务；错过的任务在条件恢复后执行。默认计划时间是本机 Asia/Shanghai。计划任务参数没有密码。

巡检数据库/网站就绪、死信、超过额外一分钟的租约、备份是否超过 24 小时、磁盘是否不足 5 GiB、TLS 是否不足 14 天。写 `operations-status.json`，存在告警时退出码为 1；可在任务计划程序查看执行结果。没有发送邮件、Slack 或其他外部通知，文件告警不等于有人值守。

认证清理只删已经过期/闲置超过 30 分钟的会话及超过 24 小时的限流键，不改变账户凭据和业务历史。正式的数据保留与告警接收人仍需团队确定。

## 本轮验证入口

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/verify_local_deployment.py
npx.cmd --prefix frontend playwright test --config frontend/playwright.local.config.ts
.\.runtime\venv312\Scripts\python.exe scripts/export_real_security_evidence.py
```

第一项验证真实 HTTPS、密码 Cookie、受限 SQL 与关键审计、网站重启后会话保留、退出及 API/前端边界。第二项只信任本机测试证书，关闭截图/trace/video，避免密码进入浏览器测试附件。第三项仍使用隔离 PostgreSQL 和本地模型替身；其静态报告不证明真实模型或公网生产环境验收通过。
