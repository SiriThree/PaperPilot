# Phase 2C：任务恢复与重复提交防护

## 检查结论及接口兼容

原工程已经有 Task Schema、创建/查询/重试 API、DataPort 幂等记录、attempts 隔离及服务端项目授权。原前端 task_id 只在组件内存中，刷新丢失；上传和报告键仅在页面内保留，问答/证据/草稿每次调用生成新键；轮询错误没有统一恢复入口。

本次复用既有 API 和 AB-1，不新增任务列表、按键查询或新 DataPort 方法。`ports.py` 的 26 方法、公共 Schema、OpenAPI 及生成类型不变；数据库中立、DataBinding 的显式注册和历史实验保留。

## 统一恢复流程

`frontend/src/composables/useTaskRecovery.ts` 供文献和研究页使用，`TaskRecoveryPanel.vue` 显示统一状态、历史任务编号、重新查询、原请求核对及明确的失败重试。

1. 提交前取得同步操作锁，读取/校验浏览器恢复记录；完成载荷指纹和固定文献范围后，先保存请求编号和最小信息，再发送 POST。保存记录失败时不发任务创建请求。
2. 成功收到 task_id 后，保存编号并通过既有 GET 查询；pending/processing 明确显示等待/处理中，succeeded 读取原结果，failed 展示服务端安全错误。重复选择已有成功任务只 GET，不生成。
3. 页面刷新自动读取当前用户/项目/页面的任务记录，仅对已有 task_id 执行 GET。结果按 process/answer/evidence/draft 应用到现有界面；不自动保存报告、不触发 AI，也不重复覆盖当前页面已应用的同次结果。
4. GET 请求 10 秒超时；每轮等待最多 60 秒。超时、断网、503 或响应不明表示“当前状态未知”，不更改服务端状态，也不把任务标记 failed。状态未确认时阻止新创建；用户可重新查询同一个编号。
5. 没有 task_id 的 submitting 记录刷新后视为未知提交，不自动 POST。用户恢复原输入后点击“使用原请求核对”，才允许用原键及原载荷重放。
6. 失败重试使用现有 `/tasks/{task_id}/retry`，先 GET 当前状态，只在仍 failed 时明确重试，仍保留同一个 task_id。它没有任务创建幂等键；确认丢失后以 GET 对账，不自动重复 retry。提交/重试请求的客户端等待上限为 15 秒。

恢复草稿时正文/引用来自服务端结果；浏览器未保存原研究问题，因此刷新恢复的草稿要求重新核对研究问题后保存。已经保存的报告仍通过既有历史版本 API 恢复正文和引用，不重新生成草稿。

## 浏览器保存什么

使用现有 Vue ref/computed 和 sessionStorage，无新增依赖。命名空间为 `paperpilot.tasks.v1.<user_id>.<project_id>.<page>`，每页最多 20 个轻量记录。

记录只含版本化命名空间、请求记录 ID、项目、页面、类型、非认证 Idempotency-Key、载荷 SHA-256 指纹、提交阶段、已知 task_id、必要的 paper_id/paper_ids。研究正文/问题、论文标题、PDF 内容、回答、来源片段、报告正文、密码及认证令牌不写入恢复存储。Cookie 继续由原 HttpOnly 会话机制管理。

记录校验类型、UUID、项目/页面、阶段和数量，额外字段不被重新保存；损坏或不可用时停止新提交，显示安全提示。页面卸载停止查询；迟到响应不能写入已卸载页面或另一个用户的状态。用户切换后使用独立命名空间，客户端编号不构成访问凭据。

sessionStorage 只支持同一标签页会话内刷新，不提供跨设备、关闭标签页后的可靠恢复。记录可能被用户清除，也可能因服务端 Mock 重启而指向不存在的任务。指纹只用于一致性判断，不是授权、认证或加密存储方案。

## 各类幂等边界

| 操作 | 客户端请求与对账 | 服务端已有保证及限制 |
|---|---|---|
| 上传 | 原键 + 文件摘要/标题指纹；刷新后必须重新选择原文件、恢复原标题 | import_paper 的用户/项目/键、摘要唯一和原 ID 回放；未知提交保留原文件，继续遵循 2B 边界 |
| 问答、草稿 | 原键 + 原问题 + 固定 paper_ids；问题不存浏览器，刷新后需要重填 | create_task 按规范化载荷幂等；文献范围变化/撤销仍须重新授权，不能承诺总可回放 |
| 证据提取 | 原键 + 原 paper_id，恢复时可直接使用已存论文编号 | 仍由 create_task 幂等；论文移出、权限变化等可能拒绝回放 |
| 查询已知任务 | 只 GET 原 task_id；失败不触发新 POST | 服务端逐次认证/项目授权；不存在和不可访问统一 404，避免泄漏存在性 |
| 重试失败任务 | 先 GET，再明确 POST retry，响应不明继续 GET | 状态约束和 attempts 隔离；不是创建请求的同键回放语义 |

客户端同步锁在任何 await 前生效，重复点击包括同一事件周期内多次派发只产生一次创建。同一载荷再次点击时优先读回既有 task_id。按钮禁用只是展示，代码锁和同键回放分别验证，不能据此声称全局 exactly-once。

问答/草稿通过已有列表 API 固定当前 searchable 文献编号，沿用 QuestionRequest 原有显式 paper_ids 参数，上限 50，不改 Schema。若集合非空，核对时不重新获取/扩大集合。原集合为空时，现有 API 的空列表仍表示服务端当前范围；若此时新论文加入，旧键可能返回 IDEMPOTENCY_CONFLICT。客户端保留不明记录，不改键重建；这需要团队后续决定空范围或按键查询的最小兼容方案。

首次收到明确的验证/权限/唯一性拒绝，或 AB-1 保证已回滚的 DATA_UNAVAILABLE，可展示“请求被拒绝”；网络/超时、DATA_COMMIT_UNKNOWN、未分类 5xx/无效确认均保留未知记录。对既有未知请求核对失败，即使收到拒绝也不推断先前没有创建任务。

没有按键查询接口时，“使用原请求核对”可能在原请求未提交时创建一个任务，因此该按钮明确是用户授权的同键重放；不是一个无副作用的查询接口。原输入丢失、幂等记录过期、权限/文献范围变化时可能无法自动解开未知状态。不会用新键盲目创建替代任务；当前需要项目负责人/A 协助对账。

## 后端与安全

继续使用 `/api/v1/projects/{project_id}/tasks/{task_id}` 和 Task Schema。project_context 每次从会话读取用户并核对角色，DataPort 再核对任务范围；未授权不返回结果或任务错误详情。404 为 RESOURCE_NOT_FOUND；明确存储故障保持原错误，未分类查询故障脱敏为 503 TASK_QUERY_UNAVAILABLE。查询不 claim、不 retry、不执行 AI，不回退 Mock。

研究任务创建发生未分类异常时，B 保守返回 DATA_COMMIT_UNKNOWN，供原键核对；不把未知提交误报为确定失败。没有新增数据库、模型或迁移。

## 测试与适用范围

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_task_recovery.py -q
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
$env:PAPERPILOT_E2E_API_PORT = '8100'
$env:PAPERPILOT_E2E_WEB_PORT = '5273'
$env:PAPERPILOT_E2E_DATA_MODE = 'mock'
npm.cmd --prefix frontend run test:e2e
```

后端测试实际验证四种创建流同键同 task_id、GET 只读且不执行 AI、查询故障、未知创建确认、匿名/非成员/跨项目/撤销成员/不存在任务。使用 Mock，只证明接口行为。

浏览器测试实际验证四种任务刷新后的原编号与原结果、请求计数、查询超时后 GET 恢复、虚拟时钟越过 60 秒不标记失败、错误展示、三种重复点击、未知上传/问答刷新及原键核对、错误输入拒绝、未知查询状态阻止新提交、浏览器存储失败阻止创建、404 不自动创建。网络和确认丢失通过拦截模拟，不是数据库故障实验。

AB-1 及历史 SQLite 仍显式运行；其结果仅属已有实验，不代表正式数据库的服务重启、并发、租约或安全恢复已通过。所有实际命令、计数及警告见 [验证记录](verification.md)。

## 下一步

与 A 对齐幂等保留期、未知任务查询/对账、空文献范围和服务重启恢复的正式验收。B 可继续完善独立于生成接口的任务历史/管理交互；需要新 API 时先审查契约，不能通过浏览器记录取代 A 的真实持久化。
