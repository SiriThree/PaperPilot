# 共享 API 契约 v0.1

Phase 3 不修改公开 Schema 或端口：C 的5方法、D 的2方法完整内部调用和异常/审计示例见 `contracts/module-port-examples.json`，由 `scripts/export_module_examples.py` 实际调用 Mock 生成并可用 --check 校验。注册、共享测试和责任边界见 [成员 C](member-c-handoff.md)、[成员 D](member-d-handoff.md) 和 [统一指南](development-guide.md)。这些不是新增 HTTP 接口，也不代表真实算法/SQL 防护已实现。

Phase 2C 复用原 Task Schema 与所有 URL，不修改 AB-1。任务 GET 的未分类数据/查询异常映射为 503 TASK_QUERY_UNAVAILABLE；任务创建的未分类提交异常保守映射 DATA_COMMIT_UNKNOWN，需原键核对。401 表示需重新认证，404 统一表示不存在或不可访问，不泄漏任务是否存在。恢复流程和幂等边界见 [任务恢复说明](task-recovery.md)。

Phase 2B 中立调整保持公开 Schema 与 AB-1 不变。上传存储尚未提交失败使用既有统一错误结构的 FILE_STORAGE_UNAVAILABLE；未分类的上传提交错误脱敏为 DATA_COMMIT_UNKNOWN 并保留文件。相同上传/报告载荷重试需沿用原 Idempotency-Key；重放返回原资源。持久化注册与对账责任详见 [中立接入说明](persistence-integration.md)。

定义源为 `backend/app/schemas.py` 及路由声明；机器契约为 `contracts/openapi.json`，类型由 `npm run contracts` 生成到 `frontend/src/api/schema.d.ts`。JSON 示例来自实际 TestClient 调用，位于 `contracts/examples/{operationId}.json`，每个操作包含 success/error。不要分别手改生成文件与前端结构。

Phase 2A 的内部数据接口基线见 [A/B 契约 AB-1](ab-data-contract.md) 与 `contracts/data-port-examples.json`。26 个 DataPort 方法调用形式及公开 HTTP Schema 保持兼容；收紧非法内部载荷、迟到任务写入和重复审计事件行为。

## 统一规则

- 基础路径 `/api/v1`；除上传和文本导出外均使用 JSON。
- user_id、project_id、paper_id、chunk_id、evidence_id、task_id、report_id、version_id、event_id、request_id 均为 UUID 字符串。
- `citation_id` 是单次结果内的局部字符串编号，如 `1`；不作为永久实体 ID。
- 时间使用带时区 ISO 8601，后端输出 UTC。
- 所有研究数据响应含 `mock`；来源含 `synthetic`。真实适配器必须明确返回 false，不能依赖 Mock 默认值。
- 登录采用开发会话 Cookie。请求编号由后端生成，或接收合法 UUID 的 `X-Request-ID`，并通过同名响应头返回。无效编号被替换；编号不是授权凭据。
- 写请求要求 `X-PaperPilot-Client: web`；浏览器 Origin 如存在必须在配置允许列表中。默认不开放跨源访问。
- 上传、问答、提取、草稿、保存报告支持可选 `Idempotency-Key`。相同用户、项目、操作和键必须使用相同参数；参数变化返回 409。未带键即视为新操作。客户端若重试写操作应复用原键。
- 项目列表/论文列表当前无分页；检索 limit 默认 10、最大 50；审计 offset 默认 0、limit 默认 20、最大 100。

## HTTP 操作

以下相对路径中的 `P` 表示 `/projects/{project_id}`。

| 方法 | 相对路径 | 返回/用途 |
|---|---|---|
| GET | /health | 运行模式与能力限制，不要求登录 |
| POST | /auth/login | 开发身份登录，仅 username，不接受密码 |
| GET | /auth/me | 当前用户和开发认证标记 |
| POST | /auth/logout | 注销当前会话 |
| GET / POST | /projects | 本人项目列表 / 创建项目，201 |
| GET / POST | P/members | 负责人读取 / 添加已有账户，201 |
| DELETE | P/members/{user_id} | 负责人移除成员，不能移除负责人 |
| GET / POST | P/papers | 年份筛选列表 / multipart 上传，202 |
| GET / DELETE | P/papers/{paper_id} | 详情与片段 / 移出项目 |
| GET | P/tasks/{task_id} | 状态、时间、错误、结果及引用 |
| POST | P/tasks/{task_id}/retry | 仅 failed 可重试，202 |
| POST | P/search | 模式 keyword/semantic/hybrid，年份筛选和 limit |
| POST | P/answers | 问答请求，202 返回任务 |
| GET / POST | P/papers/{paper_id}/evidence | 已提取证据列表 / 提取任务，202 |
| POST | P/report-drafts | 草稿任务，202；不保存报告版本 |
| GET / POST | P/reports | 最新报告列表 / 保存新版本，201 |
| GET | P/reports/{report_id}/versions | 完整历史版本 |
| GET | P/reports/{report_id}/versions/{version_id}/text | text/plain 导出 |
| POST | P/secure-queries | D 网关结果，区分策略与执行状态 |
| GET | P/audit-events | 负责人审计筛选和分页 |

移出论文必须提供 `confirm_source_impact=true`，否则返回 409 并提示历史引用影响。文件上传上限为 20 MiB，Mock 仅检查 PDF 标记，不完成 PDF 安全和正文识别。

## 统一错误

```json
{
  "code": "RESOURCE_NOT_FOUND",
  "message": "资源不存在或不可访问",
  "request_id": "00000000-0000-4000-8000-000000000001"
}
```

可选 `details` 为字符串键值对象。错误不能回显验证器的原始输入、内部异常、未授权标题、SQL 或正文。

| HTTP | 错误码 | 语义 |
|---|---|---|
| 400 | INVALID_IDEMPOTENCY_KEY / VALIDATION_ERROR | 请求头不符合约定 |
| 401 | UNAUTHENTICATED / INVALID_IDENTITY | 未登录或演示账户不存在 |
| 403 | OWNER_REQUIRED / CSRF_REJECTED | 角色不足或来源校验失败 |
| 404 | RESOURCE_NOT_FOUND / USER_NOT_FOUND | 不存在或不可访问；仅负责人可获账户查找结果 |
| 409 | IDEMPOTENCY_CONFLICT / DUPLICATE_PAPER / VERSION_CONFLICT | 幂等、导入或版本冲突 |
| 409 | OWNER_IMMUTABLE / TASK_NOT_RETRYABLE / SOURCE_IMPACT_CONFIRMATION_REQUIRED | 业务前置条件未满足 |
| 413 | UPLOAD_TOO_LARGE | 请求或文件过大 |
| 415 | UNSUPPORTED_FILE | 非 PDF 标记 |
| 422 | VALIDATION_ERROR | 参数校验失败，不回显原输入 |
| 422 | DATA_CONTRACT_ERROR | 内部数据访问载荷不符合冻结契约 |
| 409 | IDENTIFIER_CONFLICT / TASK_STATE_CONFLICT / AUDIT_EVENT_CONFLICT | 标识符占用、旧任务尝试或事件内容冲突 |
| 502 | INVALID_CITATION / ADAPTER_CONTRACT_ERROR | 模块输出或来源不符合契约 |
| 503 | GATEWAY_UNAVAILABLE / AUDIT_UNAVAILABLE | 必需模块不可用，拒绝返回执行结果 |
| 503 | DATA_UNAVAILABLE / DATA_COMMIT_UNKNOWN | 已确认回滚的存储故障 / 提交结果未知；后者必须保留原幂等键对账 |
| 500 | INTERNAL_ERROR | 脱敏内部错误 |

任务内 `TaskError` 只含 code/message；关联 request_id 在任务外层。AI_UNAVAILABLE 是可重试的 Mock 任务错误，不代表 HTTP 请求创建失败。

## 任务

状态固定为 pending → processing → succeeded/failed；仅 failed → pending 可重试。任务包含创建/更新时间、attempts、project_id、request_id、error、result_ref 和带 kind 区分的 result。创建接口 202 不代表处理已经完成。

领取后的 Job 是分离快照，attempts 单调增加；complete_job/fail_job 必须拒绝旧执行次数覆盖。业务成功审计在提交后记录，requested/pending 只表示尝试。B 的独立审计调用不能假定与业务方法共享数据库事务；真实层须交付事务内领域事件或 outbox，详见 AB-1。

重试保持 task_id 和 created_at，采用当前请求的可信上下文，更新 request_id 并记录 task.retry_requested；历史请求仍可通过审计关联。已失去权限的请求不能发起重试。

结果 kind 分为 process、answer、evidence、draft。问答和草稿的结果资源编号暂用 task_id；提取结果用 evidence_id；论文处理用 paper_id。响应不包含内部文件路径、执行参数或原始内部异常。

## 来源、问答与证据

Source 至少包括 citation_id、paper_id、chunk_id、page_number（从 1 开始）、excerpt 和 synthetic。

C 的检索必须按授权项目读取。B 将返回的论文、片段、页码和摘录与 A 的授权数据核对；问答引用还必须属于本次提供给 C 的检索集合。不能把任意模型文本变成来源。

AnswerResult 包含 answer、citation_ids、sources、insufficient_evidence。非不足回答至少有一条有效引用，citation_ids 与 sources 一一对应。Mock 的“证据不足”触发空来源响应。

EvidenceResult 包含 research_question/method/results/conclusion/limitations；每项有 text、status 和 sources。status 为 pending_verification、not_mentioned、insufficient_evidence。待核验字段必须有文本及来源。

## 报告

ReportSave 接收 title、research_question、body、sources，可选 report_id、base_version_id。新报告二者为空；更新报告必须提交最新基础版本。A 在同一事务保存正文、版本号、问题及引用快照，不能覆盖历史版本。版本过期返回 409。历史来源快照是项目授权数据，不能放到公开 URL。

## 网关及拒绝语义

前端仅提交 question，用户身份和项目成员关系由服务端确认，内部 `Context` 传递 user_id/project_id/request_id。前端没有 SQL 文本输入或数据库连接能力。

| decision | execution_status | 含义 |
|---|---|---|
| allowed | executed | 策略允许且执行完成；仅此组合可以展示结果 |
| allowed | not_executed | 已允许但尚未执行；不应显示为成功 |
| rejected | not_executed | 明确拒绝，必须有 reason，无结果 |
| undetermined | failed | 执行或判断失败，必须有 reason，无结果 |

策略拒绝及执行状态均是业务结果，HTTP 200 不表示执行成功。网关不可达使用 HTTP 503 + 统一错误。关键审计故障同样 503。未执行的结果不允许包含 columns/rows。

Mock 仅精确支持：按年份统计论文数量、按研究主题统计论文数量、查询尚未完成证据提取的论文。未提取列表现在排除已保存 EvidenceResult 的论文；仍不是实际 AI 提取质量或覆盖率评测，主题仍为固定合成主题。

D 真实实现必须自己完成 SQL AST 校验、允许清单、项目绑定、受限数据库身份、默认 3 秒超时、最多 100 行和响应体限制、执行前关键审计。B 不提供直接执行 SQL 的降级路径。

## 审计

字段包括 event_id/timestamp/request_id/actor_id/project_id/action/resource_type/decision/policy_id/execution_status/duration_ms/mock。登录事件的 actor_id 或 project_id 可为空；它们不会通过项目列表接口泄露。

筛选：action、decision、actor_id、since、until、offset、limit。时间必须带时区、since 不晚于 until。普通成员不能读完整审计，不存在公开审计修改/删除接口。Mock 记录不具有持久化、防篡改或完整安全实验效力。
