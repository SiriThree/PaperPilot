# 成员 D 当前进度

更新于 2026-10-29。当前以 main 合并基线 `2586767` 加本地改动验收，最新结果见 [D 验收清单](member-d-acceptance-20261029.md)、[威胁模型](member-d-threat-model.md)和[安全实验](security-evaluation/README.md)。当前全量 985 通过、34 跳过、0 失败；下文 10 月 28 日的计数和团队快照保留为历史记录，不作为当前验收。未推送、未修改远程 PR，公开 AIPort、DataPort、SecurityPort 及 HTTP Schema 不变。

## 本轮只读核对并合入的团队快照

| 成员 | 固定状态/参考 | 本地影响 |
|---|---|---|
| A / #1 | 已合并；head `195db34` | PostgreSQL/pgvector、迁移、DataPort、outbox 已在当前仓库运行 |
| B / #3、#5 | 已合并 | 研究与安全页面、任务恢复、Mock 证据报告已同步 |
| C / #4 | 已合并；head `1f43093` | 真实解析、检索与模型组合入口已同步；不提供通用 NL2SQL |
| D / #2 | 已合并；head `f3f3a39` | 远程仅含较早安全实现，后续本地交付尚未推送 |
| main | `14701d50df9fc13a0578aacc712e38445ae5d7ef` | 已合并到本地 D 分支，未改远程 |

此前 Open/Draft、固定外部工作树及 645/37 等说明属于历史验收快照，不再表示当前合并状态。原固定参考验证入口仍保留，当前合并验证使用显式 `PAPERPILOT_SECURITY_CURRENT_SOURCE=1`。

## D 原任务 1—9

| 任务 | 开发与本地隔离验收 |
|---|---|
| 1 接口与工厂 | SecurityAdapter/build_security/真实组合启动完成 |
| 2 应用授权 | 重新查询成员角色、快照核对、默认拒绝、即时撤权完成 |
| 3 统计安全策略 | 三个固定意图、完整 AST 允许清单、可信项目参数重建完成 |
| 4/5 执行与结果 | 独立受限登录、只读、3 秒、100 行、256 KiB、脱敏、取消清理完成 |
| 6 关键审计 | 执行前/后确认、失败关闭、event_id 幂等及独立最小写身份完成 |
| 7 SecurityHarness | 真实 PostgreSQL provider，包含在合并全量验证中 |
| 8 攻防 | 44 攻击、44 直接绕过、21 正常及隔离/权限/资源故障覆盖完成 |
| 9 团队联调 | 合并 A+D/C=Mock、A+C+D、实际启动与浏览器验证完成；模型为本地替身 |

数据库隔离仍为 security_barrier 视图加 SESSION_USER 项目绑定，不能称作底表原生 RLS。默认 `build_security(data)` 无可信 readers 时继续不执行 SQL，真实入口显式配置后执行。

## 本轮追加工作

1. 完成遗留 C 合并并同步 A/B/C main，合并依赖和 SQL 包资源；新增当前代码的一次性联合验证/预览工具。
2. 本地后续交付说明见 [followup](member-d-followup.md)，未创建/修改远程 PR。
3. 真实适配器浏览器链路及真实报告错误边界 6 项通过，原 Mock 阅读/证据流程 11 项通过。
4. 指定论文在候选生成时过滤，再排序/截取；不再受全项目前 50 条影响。
5. A/C 私有原子索引提交、正文块/模型版本/维度/指纹、重启复用、明确重建与历史保留完成，见[索引](persistent-index.md)。
6. 项目 reader、audit、business 三种独立登录及生命周期在隔离库验证；应用业务身份不再需要管理员权限，见[业务身份](business-login.md)。
7. 任务心跳与恢复上限、outbox token/租约、死信及明确重放/计数完成，见[恢复](runtime-recovery.md)。不自动删除审计、outbox 或幂等历史。
8. 静态真实报告区分 PostgreSQL 与 Mock；来源、覆盖不全、跳过、失败均不能冒充通过，见[真实报告](real-security-evidence.md)。

## 复现与仍待实际环境落实的事项

使用 Python 3.12、Docker：`python scripts/verify_integration.py --business-login`；完整真实报告由 `python scripts/export_real_security_evidence.py` 实际运行后生成。资源全部为自动清理的一次性数据库，不接触业务库。最终计数见报告 `verification_totals`，31 项历史 SQLite 实验始终跳过，不计为通过。

最终全量 **759 passed / 31 skipped / 0 failed**；其中真实报告分组 241 项已包含在总数中。真实浏览器 6 项和既有 Mock 阅读/证据 11 项通过，前端类型/构建、全目录 Ruff lint、本轮改动文件格式、公开契约、依赖和 wheel 资源检查通过。全目录格式仍有 8 份未改动 main 文件的既存差异，未记作通过，见[验证限制](real-security-evidence.md)。保留既有 Starlette/httpx 弃用与 Vite 大包提示。

仍需团队环境决定并实际执行：正式库迁移与 D 资源安装、所有权/账号凭据分发、生产认证、真实外部模型质量与性能、备份/保留/告警运营策略。本文不把代码或隔离测试等同于生产部署；不需要等待其他成员才能继续本地开发，但不能用本地结果替代这些实际验收。
