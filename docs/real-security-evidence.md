# 真实安全证据报告

`scripts/export_real_security_evidence.py` 在一次性 PostgreSQL 16 中运行当前合并代码的全量验证，生成 `frontend/public/security-evidence-postgres.json`。默认 Mock 报告仍由原工具独立生成，不把 Mock 的通过数充作真实数据库证据。

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/export_real_security_evidence.py
```

本轮最终全量 **759 passed / 31 skipped / 0 failed**。31 项跳过全部为历史 SQLite 实验，不计为通过。报告选取实际 PostgreSQL 组共 **241 项**（包含在 759 中，不能再相加）：SQL 执行/资源 146、数据库统计/项目隔离 27、审计/身份/业务降权 40、索引/恢复/团队组合 28。模型端点为本地替身，不是外部模型质量证据。

导出器从测试文件解析预期测试函数，要求攻击至少 44、直接执行器绕过至少 44、正常变体至少 21；缺失、跳过和失败分别保留，不补造通过数。全量中即使是分组外测试失败，也会阻止通过徽标，保留真实计数。来源必须是 current_checkout、相同 Git 提交、实际 PostgreSQL 16 版本和固定摘要镜像。执行前后检查代码指纹与 HEAD，运行中源码改变则拒绝覆盖报告；无新 JUnit 也拒绝覆盖。报告只含计数、测试文件、版本/指纹、来源及环境限制，不发布原始候选 SQL、异常正文、凭据或业务内容。JUnit 留在忽略的 `.runtime`，报告记录相对路径供本地复核。

网关为真实 D 时页面读取 PostgreSQL 报告；Mock 模式继续读取 Mock 报告。页面严格校验 scope/schema、分组、计数及来源；报告缺失、伪造来源、攻击覆盖不足均不能显示通过。加载失败会隐藏旧的通过徽标。它是静态离线记录，不验证当前运行实例的凭据、数据库配置或生产认证。

验证：导出计数/来源门槛 7 项单元测试；真实适配器浏览器 6 项（完整流程加报告错误边界）；既有 Mock 阅读/证据 11 项；前端 typecheck/build、公开 OpenAPI、依赖与 wheel 的 6 份 A/D SQL 资源检查通过。全目录 Ruff lint、本轮改动 Python 文件的格式检查通过。全目录 format 尚有 8 份未改动 main 基线文件的既存差异，未计作通过，具体为 mapping/provider、outbox/persistent_integration 测试及 benchmark_postgres/render_chen_clean/verify_backup_restore/verify_constraints_day2；与 origin/main 对照无改动。保留 Starlette/httpx 弃用和 Vite 大包提示。

末轮曾发现 C 的 20 毫秒超时测试在授权阶段结束前就耗尽预算，却要求未启动的模型完成清理。已保留整体超时断言，并在模型实际启动后单独验证主动取消和清理；C 真实契约 56 项及该用例连续 5 次复验通过。

报告生成时有明确的未提交工作区标记及代码指纹，后续本地提交不把它冒称为新提交上重新运行的报告。指纹覆盖 backend 源码/测试/迁移、验证/重建/导出工具、依赖配置和证据组件/页面，报告文件本身不参与，避免自引用。
