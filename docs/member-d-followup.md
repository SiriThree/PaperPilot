# 后续本地交付说明

本文件是可直接审阅的交付正文，未创建或修改远程 PR，未推送。

建议标题：`feat(integration): complete PostgreSQL execution, persistent retrieval and recovery`

## 问题与结果

已合并的 D #2 只含较早的安全校验/审计门槛。此后本地交付提供显式真实执行、独立审计/读取/业务身份及组合入口；合并 A/B/C main 后可用一次性数据库重现完整链路。指定论文检索先限定范围，避免目标来源被全项目前 50 条挤出；解析文本、版本化向量、process 结果和 ready 在同一尝试内原子提交，重启复用，并可明确重建。任务失联恢复和 outbox 投递都有预算、领取隔离及明确人工恢复入口。页面真实证据来自实际 PostgreSQL 检查，失败或覆盖不全不会显示通过。

公开 DataPort/AIPort/SecurityPort 和 HTTP Schema 保持不变，新增协作方法只用于已组合的 A/C 实现。默认未配置可信 reader 时 D 仍不执行；真实模式失败不降级 Mock。数据库边界是 SESSION_USER 绑定的安全屏障视图，未声称底表原生 RLS。

## 验证

- Python 3.12，全量真实 provider **759 passed / 31 skipped**，31 项仅历史 SQLite；来源和代码指纹见[实际报告](../frontend/public/security-evidence-postgres.json)。其中 241 项真实安全/身份/索引/恢复组已包含在总数内。
- 真实 A/C/D 非管理员业务连接浏览器 6 项、既有 Mock 阅读/证据 11 项通过；C 模型 HTTP 为本地替身。
- 全目录 Ruff lint、本轮改动文件格式、OpenAPI、依赖、wheel 迁移资源、前端 typecheck/build 通过。全目录 format 仍有 8 份未改动 main 文件的既存差异，见[验证限制](real-security-evidence.md)。测试/预览只用生成的 PDF 和一次性数据库。

## 安装与交付边界

正式应用前需管理员显式执行 A 的 `002_process_indexes.sql`、`003_recovery.sql`；已安装 D 002 的环境还须显式升级 D `003_business_login.sql`。首次 D 安装会撤销数据库 PUBLIC CREATE/TEMP 和 public schema CREATE，应先审核数据库范围，不能直接对未知业务库运行。应用业务账号、审计写账号、每项目 reader 使用独立凭据；生命周期命令、配置及复现见[业务账号](business-login.md)、[启动](member-d-startup.md)、[索引](persistent-index.md)、[恢复](runtime-recovery.md)和[集成记录](member-d-merged-integration.md)。

未执行正式业务库变更、生产认证、外部模型质量验收或大规模性能验收；备份、保留、告警运营策略仍由团队落实。没有自动清理历史数据，也没有自动重放死信。

本地审阅范围：`git log --oneline origin/main..HEAD`、`git diff --stat origin/main..HEAD`；这两个命令只查看本地已缓存的 main 快照，不会修改远程。
