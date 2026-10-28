# 合并后 A/C/D 集成记录

2026-10-28：本地分支已合并 `origin/main@14701d5`；A #1、B #3/#5、C #4、D #2 均已合并。远程 D #2 只包含较早的安全实现；后续执行器、独立审计身份与启动入口仍为本地交付，未推送或修改远程 PR。

## 复现

使用 Python 3.12 和 Docker，运行：

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/verify_integration.py
```

脚本创建并清理一次性 PostgreSQL，明确选择当前工作区的 A/C/D 实现和真实 Data/Security provider；C 的模型端点为可控 HTTP 替身。凭据仅在进程环境中传递，不连接业务库。原有固定参考 provider 模式保留。

合并基线：**719 passed / 31 skipped / 0 failed**，报告 `.runtime/d-main-integration-results.xml`。31 项历史 SQLite 实验跳过，不计作通过。另行定向 A 数据契约、outbox 和持久化流程共 55 项通过，已包含在全量内。前端 typecheck/build 通过，保留既有大包提示和 Starlette/httpx 弃用提示。

`scripts/preview_integration.py --state .runtime/preview.json` 可启动一次性真实适配器预览，创建生成的 PDF、受限 reader/audit 登录和本地模型替身；状态文件只含访问地址、项目/论文 ID 和日志路径。创建对应 `.stop` 文件结束预览并清理容器。它不验证外部模型效果或生产部署。

## 后续交付范围

依次处理指定论文范围内检索、原子持久化索引、隔离环境业务登录降权、任务租约/重试及 outbox 死信恢复、真实安全证据报告。生产认证、正式数据库安装及外部模型质量需要实际环境验收，不在此处冒充完成。
