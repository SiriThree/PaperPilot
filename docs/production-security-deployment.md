# 本机 HTTPS 生产安全配置

当前组合为 `ENV=production`、A 持久化、C 真实文献 AI、D 原生 PostgreSQL，保留独立认证与关键审计。已导入 6 篇真实论文，统计与文献生成使用现有模型，本地 BGE 负责向量。当前结果和完整演示步骤见 [C/D 联合 HTTPS 验收](member-c-d-https-acceptance-20261031.md)。仅配置统计模型而没有研究模型时，C 入口继续明确不可用。

已准备的本地嵌入文件与清单位于 `.runtime/production/embedding-model/`；清单绑定固定版本和逐文件 SHA-256。首次增加 C 时使用 `production_deployment.py up --research-model-config <现有受保护模型JSON的绝对路径>`；应用启动不下载权重。后续本机 `up` 会保留已配置的 C，更新前仍须备份。

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
| 模型与文献 AI | 查看顶部提示及查询来源 | 统计模型、文献生成模型和本地向量分别标识；进入真实文献项目演示 |
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

C 启用后，备份还包含本地模型快照与上传文件归档；六篇文献按固定 PDF 哈希核对，上传文件在独立目录解包复验，在无外网恢复环境验证本地关键词／语义检索。演练不调用外部生成模型，不覆盖正式卷。

备份目录包含密码、角色验证信息和 CA 私钥，只能保存在受保护的本地目录，不能提交到 Git。SHA-256 清单用于文件一致性核对，不提供对恶意篡改的独立认证。没有完整 manifest 的目录是未完成备份，不可当作已验收的恢复点。脚本只对本轮自建、带随机所有权标签的隔离恢复容器执行清理，不向正式数据库执行恢复。

## 当前限制

原有三个项目与新增两个文献项目均已供应原生读取身份。网页新增项目不会自动获得数据库读取身份，统计接口返回 `PROJECT_READER_NOT_PROVISIONED`。管理员可显式供应当前部署 owner 的已有项目：

```powershell
docker compose --env-file .runtime/production/compose.env -f compose.production.yaml run --rm setup bind-project-reader --project-id <项目UUID>
docker compose --env-file .runtime/production/compose.env -f compose.production.yaml restart backend
```

本次不包含 300 篇全集导入、大规模性能、公网域名、所有业务底表原生 RLS 或旧环境数据迁移。少量模型正常／拒绝用例和真实文献工作流验收不代表完整语义准确率测量。业务项目授权仍由应用确认；D 统计查询额外使用受限数据库身份和项目视图。历史回归证据与当前部署验收在页面分开显示。
