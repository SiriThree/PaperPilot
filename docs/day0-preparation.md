# 成员 A 在 10 月 26 日完成的开发准备

今天完成 PostgreSQL 16 与 pgvector 的独立环境预验证，并确定数据层的临时默认配置。来源为 SiriThree/PaperPilot 的 main 源码快照，基准提交为 `85fe82ddd51f74d03bd1137dc392e3cf052968fb`。固定开发目录为 `D:\PaperPilot`，使用本地 `member-a` 分支。本次准备工作整理为本地提交，尚未推送到 GitHub；后续在此目录更新、验证后创建本地 commit。

## 实测结果

| 项目 | 结果 |
| --- | --- |
| PostgreSQL | 16.15，独立 Docker 容器，127.0.0.1:54326 |
| pgvector | 0.8.7，镜像摘要固定在 compose.day0.yaml |
| psycopg3 | 3.3.6，独立 Python 3.12 虚拟环境 |
| 事务 | 提交记录保留，主动异常导致的记录回滚 |
| 向量列与相似度 | vector(3) 实测；余弦相似度排序为 1.0、0.0 |
| 双角色 RLS | 分别使用两个非超级用户、无 BYPASSRLS 的真实登录连接；无应用过滤时也只能读取本项目，允许本项目写入，拒绝跨项目写入 |
| 只读角色 | 可读本项目；关闭会话只读默认值后，INSERT、UPDATE、DELETE、普通及临时建表、切换业务角色仍被数据库权限拒绝 |
| 后端回归 | 158 passed，37 skipped，1 warning；新增 8 项配置测试包含在通过数中 |

机器可读原始结果见 `day0-verification.json`。37 个跳过项是 6 项尚需正式 DataPort provider 的数据库门槛和 31 项历史 SQLite 实验，均不计入通过。现有 Starlette TestClient 有 httpx 弃用提示，无测试失败。本次未改前端，未运行前端测试。

预验证脚本每次创建并清理自己的随机 schema 和三个临时登录角色。仅使用专用 `paperpilot_day0_preflight` 数据库；不要改成业务数据库。角色与项目的映射是验证样例，尚未实现正式用户、成员关系或连接池项目上下文方案，也不替代 D 的安全网关和攻防测试。

## 向量和租约默认值

唯一准备配置入口为 `backend/app/data/settings.py`。

- `mock_ai.py` 仅生成合成来源片段，不生成 embedding；无法从中确认真实模型维度。
- `EMBEDDING_DIM = 3` 仅用于合成预验证，明确等待 C 确认真实模型。选定模型后修改此行；已有数据库列及向量数据仍需迁移和重新嵌入。
- 租约默认 120 秒，沿用现有历史实验值，通过 `PAPERPILOT_LEASE_SECONDS` 覆盖，接受 1—3600 秒。
- 幂等记录演示期无限保留，用 `None` 表示，不启用自动过期或清理。租约到期不表示可以删除幂等记录。
- `DataSettings.from_env()` 是后续 A 适配器应显式读取的配置；本次没有实现完整 PostgreSQL DataPort，也没有改变 B 的 Mock 默认启动方式。租约执行和幂等存储将在正式适配器中接入。

## 复跑

在仓库根目录的 PowerShell 中执行。已有 `.venv` 时可省略创建步骤。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -r requirements-day0.lock
.\scripts\start-postgres-day0.ps1
.\.venv\Scripts\python.exe scripts/verify_postgres_day0.py
.\.venv\Scripts\python.exe -m pytest -q -rs --basetemp .runtime/pytest-local
```

若 Docker 当前上下文不同，使用：

```powershell
.\scripts\start-postgres-day0.ps1 -DockerHostUri 'npipe:////./pipe/dockerDesktopLinuxEngine'
```

本机系统 Temp 下的 pytest 目录权限异常，因此使用仓库内 `.runtime/pytest-local` 作为专用测试临时目录；pytest 会在复跑时清理该专用目录，不要存入业务数据。

启动脚本首次生成 `.runtime/day0.env`，存放随机本地密码与端口，已由 `.gitignore` 排除。密码不输出到报告。使用 Docker 持久卷保留预验证数据库；重启不要重新生成密码文件。停止环境可运行：

```powershell
docker compose --env-file .runtime/day0.env -f compose.day0.yaml stop
```

## 本机 Docker 启动故障及恢复

Docker Desktop 4.54.0 启动时报告 Inference manager 无法移除 `dockerInference`。本机该文件是 2025 年遗留的零字节 ReparsePoint，与 Docker 官方仓库中的残留 AF_UNIX 套接字故障报告一致。

2026-10-26 21:41（北京时间），退出故障 Docker 进程后，仅将以下运行目录改名保留，并让 Docker 重新创建运行目录：

- `%LOCALAPPDATA%\Docker\run` → `run.stale-20261026-214108`
- `%LOCALAPPDATA%\docker-secrets-engine` → `docker-secrets-engine.stale-20261026-214108`

未恢复出厂、未注销 WSL、未删除镜像/容器/数据卷或虚拟磁盘。随后成功读取 Docker Linux Engine 29.1.2，并运行本次 PG 容器完成全部四项验证。备份目录暂时保留。

参考：[Docker 残留套接字故障及父目录改名方案](https://github.com/docker/desktop-feedback/issues/554)、[pgvector 官方文档](https://github.com/pgvector/pgvector)。

## 下一步

基于上述环境实现正式关系模型、迁移、26 个 DataPort 方法及真实数据库 provider；与 C 确定完整正文/向量载荷，与 D 确定正式角色、受控视图、项目隔离和审计保护。今天的四项预验证不代表这些后续任务已经完成。
