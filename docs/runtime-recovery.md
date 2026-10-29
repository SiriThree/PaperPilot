# 任务与 outbox 恢复

显式执行 A 的迁移工具安装 `003_recovery.sql`，历史 001/002 校验和保持不变。应用启动只检查版本，不自动迁移。

## 任务

`PAPERPILOT_LEASE_SECONDS` 默认 120 秒，持久化 worker 每租约的三分之一续期。续期仅限当前、尚未过期的 attempt，并复查成员身份；失去续期确认会取消并清理当前 AI 工作，不发布结果，交给租约恢复。完成提交也要求当前租约有效，旧 attempt 无法覆盖新状态。

`PAPERPILOT_MAX_AUTOMATIC_ATTEMPTS` 默认 3（初次执行加两次失联恢复）。达到上限记录 `TASK_RETRY_LIMIT` 和失败 outbox；用户可在现有任务恢复界面明确重试。手动重试重新开始自动恢复预算，attempt 总数仍累积，不清除历史尝试。普通业务/模型明确失败沿用显式重试，不自动反复请求模型。

## Outbox

独立投递器默认每事件最多 5 次自动领取、30 秒领取租约、指数退避。数据库条件更新生成独立 token，旧投递者不能确认/覆盖新领取；审计仍按原 event_id 幂等。失败或进程失联耗尽预算进入死信，不删除或改写原 payload。达到死信前的未知成功需要人工核对，重放仍用相同 event_id 防重复。

配置应用的非管理员业务连接环境变量并设置 `$env:PYTHONPATH='backend'`，使用 Python 3.12：

```powershell
python -m app.data.deliverer --status
python -m app.data.deliverer --once --max-attempts 5
python -m app.data.deliverer --replay-dead <事件UUID>
```

`--status` 只输出 pending/published/dead/in-flight/手动重放次数、任务状态计数及过期租约数，不输出事件内容。先核对故障原因再明确重放；重放只释放一条死信，保留累计投递次数、事件身份及时间记录，后续投递器负责实际投递。不自动重放死信，不自动删除已投递、审计或幂等数据。

这是本地开发交付的有界恢复默认值，可通过部署配置调整预算；正式保留时长、告警渠道及运营责任仍需团队确定。异步 outbox 始终不能替代 D 查询前后的关键审计确认。

验证：定向任务/outbox/索引 **15 passed**；追加非法向量边界及非管理员数据契约后全量 **748 passed / 31 skipped / 0 failed**，报告 `.runtime/d-runtime-full.xml`。31 项历史 SQLite 实验跳过。真实库验证包括续期、自动恢复上限、明确重试、旧 attempt/token、进程失联、死信重放、幂等审计和提交回滚，模型仍用本地替身。
