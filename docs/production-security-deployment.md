# 本机 HTTPS 生产安全配置

本配置服务于成员 D 的真实安全链路部署：`ENV=production`、A 持久化、D 原生 PostgreSQL、独立认证与关键审计。D 可独立配置真实统计 SQL 模型；C 文献处理 AI 仍禁用，上传处理、检索、问答、证据提取和报告生成返回 `503 AI_NOT_CONFIGURED`。

## 将统计模型接入现有 HTTPS 项目

复用已保存的受保护模型 JSON，不复制密钥到命令行或 Git：

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_backup.py
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py up --sql-model-config "D:\Study\LZZ的知识库\大三上\数据库原理及安全\大作业\PaperPilot\.runtime\local-model\model-config.json"
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py verify
```

JSON 字段为 `base_url`、`api_key`、`chat_model`，可选 `timeout_seconds`、`response_format`、`token_limit_parameter`；仅支持 HTTPS 服务。脚本校验后将配置存入受保护状态目录，并供应到 Linux 专用凭据卷中，文件归 UID 10001、权限 0600。Compose 中不包含 API Key，宿主机环境变量不会自动传入容器。

模型只接收统计问题与三类允许 SQL 模板，不接收数据库密码、论文正文或真实项目编号。候选仍经过 AST 校验、服务端项目参数绑定和独立受限读取身份执行；模型错误会失败并审计，不会静默回退。后端增加模型出站网络，数据库仍处于内部网络且无宿主端口。

模型模式的健康检查显示 `statistics_sql_mode=model`，查询与审计的 `policy_id=postgres-statistics-v2-model`。代码、模型、服务地址、请求格式或密钥发生变化后，旧验收记录不再匹配，必须重新 `verify`。运行预检不主动调用外部模型，实际可用性以查询结果与验收时间为准。

## 访问与演示

地址：<https://localhost:8443>，账号 `owner`。密码只保存在受当前 Windows 用户 ACL 保护的 `.runtime/production/owner-credentials.txt`，不写入 Git 或报告。部署脚本将本部署的本地 CA 加入**当前用户**的信任根；不使用跳过 TLS 校验的方式验收。

登录后进入“安全验收样例 A（非研究数据）”→“查询与审计”：

| 演示内容 | 操作 | 预期结果 |
| --- | --- | --- |
| 当前配置 | 点击“重新检查当前服务” | 认证、业务权限、项目读取身份与独立审计预检通过；显示匹配部署编号和代码指纹的验收记录 |
| 真实查询 | 提交“按年份统计论文数量” | `allowed / executed`，真实 PostgreSQL 数据，显示请求编号 |
| 三类允许查询 | 分别选择年份、研究主题、尚未完成证据提取，也可输入不同表述 | 模型生成候选后，仅允许三种审核过的统计结构；模板模式会单独标识 |
| 拒绝执行 | 选择“不支持的问题”并提交 | `rejected / not_executed`，不返回查询数据 |
| 操作审计 | 查询后刷新审计记录并查看详情 | 查看请求的问题、实际 SQL、结果状态及关联请求编号 |
| 模型与文献 AI | 查看顶部提示及查询来源 | 统计模型与文献处理 AI 分别标识，后者仍不可用 |
| 分层攻防 | 在独立演示环境运行原有实时实验 | 生产站点禁止启动 Docker 实验；生产镜像不包含 Docker 控制能力 |

跨项目、原生数据库拒绝、审计故障、CSRF、限流和会话重启保留等验收由下面的 `verify` 命令实际执行。它会短暂制造受控审计权限漂移、重启本轮后端，并最终修复权限、禁用验收账号和撤销其会话。操作前暂停演示，避免与其他管理操作同时运行。

## 操作命令

在仓库根目录使用 PowerShell：

```powershell
# 查看现状
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py status

# 首次部署或更新代码：构建前先备份已有部署
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_backup.py
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py up

# 验收当前 HTTPS 部署
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py verify

# 停止服务，保留持久卷
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py stop
```

首次部署尚无 manifest 时，直接执行 `up`。更新后应重新 `verify`；旧代码指纹的验收不会显示为新版本通过。Docker Desktop 需运行；上述命令不要与清理容器、重启 Docker 或其他数据库维护同时执行。

## 数据与备份边界

部署名 `paperpilot-production`，使用新建的 `production-postgres` 持久卷。旧 `paperpilot-local_postgres` 和已有演示数据库保留，未迁入、重置或覆盖。只有代理的 `127.0.0.1:8443` 对宿主机开放，数据库和后端无宿主端口。本配置仅绑定本机，不宣称已完成公网部署验收。

备份脚本短暂停止代理和后端，保存数据库、数据库级 ACL、角色、运行凭据、配置和 Caddy 私有 PKI，然后在 `--network none` 的新临时 PostgreSQL 中恢复。校验表计数、迁移指纹、认证与权限预检，并执行原生查询和独立审计。恢复时按角色名重新绑定角色 OID。通过后生成 `manifest.json` 和 `.runtime/production/evidence/backup-restore.json`，最后恢复服务。

备份目录包含密码、角色验证信息和 CA 私钥，只能保存在受保护的本地目录，不能提交到 Git。SHA-256 清单用于文件一致性核对，不提供对恶意篡改的独立认证。没有完整 manifest 的目录是未完成备份，不可当作已验收的恢复点。脚本只对本轮自建、带随机所有权标签的隔离恢复容器执行清理，不向正式数据库执行恢复。

## 当前限制

项目原生读取身份预先绑定本轮供应的三个项目。网页新增项目不会自动获得数据库读取身份，统计接口返回 `PROJECT_READER_NOT_PROVISIONED` 并拒绝执行；在增加经过审查的供应流程前，演示使用已有项目，不手工扩大共享登录权限。

本次不包含文献 AI、大规模性能、公网域名、所有业务底表原生 RLS 或旧环境数据迁移。统计模型的少量正常／拒绝用例验收也不代表完整语义准确率测量。业务项目授权仍由应用确认；D 统计查询额外使用受限数据库身份和项目视图。历史回归证据与当前部署验收在页面分开显示。
