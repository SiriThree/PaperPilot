# 成员 C 开工交接

> C 已基于 B PR #3 合入后的代码交付 `app.ai.adapter:build_ai` 和 `app.ai.bootstrap:create_app`。实际实现、交大 API/本地嵌入配置、A 分支对接边界见 [C 交付说明](ai-adapter.md)，真实论文评测见 [评测记录](ai-evaluation/README.md)。下文保留公共端口契约与责任分工。

## 1. 职责范围

负责论文解析/片段与页码、关键词/语义/组合检索、证据提取、问答与报告草稿算法、模型配置和效果评测。只实现 AIPort，向 B 返回公共 Schema；不改前端，不自行保存业务报告或实现数据库持久化。默认启动仍为合成 Mock；真实 C 需显式配置并注册。

## 2. 代码入口与建议位置

- 端口：`backend/app/adapters/ports.py:AIPort`；类型：`backend/app/schemas.py`。
- 参考形状：`adapters/mock_ai.py`；调用与严格来源校验：`services.py`；HTTP 映射：`routes/research.py`、`literature.py`。
- 实现：`backend/app/ai/adapter.py` 及模块内解析/检索/生成实现。独立测试：`backend/tests/ai_contracts/`。
- 七个 C/D 方法及场景的实际 Mock 示例：`contracts/module-port-examples.json`。HTTP Schema 在 `contracts/openapi.json` 的 components/schemas 中，没有独立的 contracts/schema.json。

## 3. 完整接口（5 个 async 方法）

```python
async def process(ctx: Context, task_id: UUID, file: FileReference) -> ProcessResult: ...
async def search(ctx: Context, query: SearchRequest) -> SearchResult: ...
async def answer(ctx: Context, question: QuestionRequest, sources: list[Source]) -> AnswerResult: ...
async def extract(ctx: Context, paper_id: UUID, sources: list[Source]) -> EvidenceResult: ...
async def draft(ctx: Context, question: QuestionRequest, sources: list[Source]) -> DraftResult: ...
```

Context 为服务端可信 user_id/project_id/request_id（均 UUID），不能从客户端自报身份构造。FileReference 为 storage_key:str、paper_id:UUID、project_id:UUID；它不是公开路径或任意 URL。

| 方法 | 责任和必须遵循的数据规则 |
|---|---|
| process | B 保存原文件并建立任务，C 返回 ProcessResult(paper_id,sources,mock)；片段必须属于该论文且页码从 1 开始、chunk_id 与 citation_id 在结果内不重复。B/A 才将来源和 searchable 状态原子提交。C 不领取或更新任务。 |
| search | 一种方法，通过 SearchRequest.mode=keyword/semantic/hybrid 区分三类检索；还含 query、year_from/year_to、limit（默认10，最多50）。返回 items=[SearchHit(paper,source,score可空)]、mode、mock、provider。必须在检索端限制项目，不全库召回后交给前端过滤。 |
| answer | 只使用本次提供的来源；AnswerResult 包含 kind=answer、answer、citation_ids、sources、insufficient_evidence、mock。非不足回答需有效来源，编号与 sources 对应；空来源时明确不足，不编引用。 |
| extract | 返回 evidence_id、paper_id、kind=evidence，以及 research_question/method/results/conclusion/limitations 五项。每项 text可空、status=pending_verification/not_mentioned/insufficient_evidence、sources；待核验项要有文字和来源。 |
| draft | 返回 kind=draft、body、sources、mock，只生成内容，不保存报告或版本；版本保存由 B 调用 A。 |

Source 字段为 citation_id:str、paper_id/chunk_id:UUID、page_number:int≥1、excerpt:str（1～4000字符）、synthetic:bool。citation_id 是当前结果内编号，不是永久数据库主键。B 对照 A 的授权数据验证论文、片段、页码、摘录和 synthetic，并验证回答/证据/草稿只引用本次提供的来源。保留输入来源标记，不能把 Mock 片段改标真实；真实处理结果明确设置 mock=false，实际来源按 provenance 设置 synthetic。

Paper 为 paper_id/project_id/title/year可空/status/mock，status=pending/processing/searchable/failed；只有 searchable 论文参与检索。Task 字段为 task_id/project_id/request_id、kind=process/answer/evidence/draft、status=pending/processing/succeeded/failed、created_at/updated_at、attempts≥0、error(code,message)可空、result_ref(resource_type,resource_id)可空、result（四种 kind 联合）可空、mock。状态、attempts、错误和结果引用由 B/A 管理，C 接收 task_id 作为关联，不自己建任务表。失败重试和迟到 worker 写入隔离不由 C 改前端实现。

## 4. 正常请求/响应示例

以下为 `module-port-examples.json` 实际执行的 answer 示例，全部合成；Sources 按真实 Schema 展示。

```json
{"ctx":{"user_id":"d9f21e57-118e-5387-8c77-36ab7e27e4cc","project_id":"d4e338d6-16c6-5fa4-b1a8-8b24baf9280f","request_id":"2083434e-6698-535f-9cf1-3f8d783cb166"},"question":{"question":"检索增强生成","paper_ids":[]},"sources":[{"citation_id":"1","paper_id":"babaff3e-8201-536d-aa17-ffb7e9b7b91d","chunk_id":"70b17c47-566d-59cd-ab8b-ad20f55ac2f8","page_number":1,"excerpt":"【合成片段】本示例展示检索增强生成的来源定位，不代表真实论文或实验结论。","synthetic":true}]}
```

```json
{"kind":"answer","answer":"【Mock 回答】合成示例演示引用与来源的对应关系 [1]，不代表真实研究结论。","citation_ids":["1"],"sources":[{"citation_id":"1","paper_id":"babaff3e-8201-536d-aa17-ffb7e9b7b91d","chunk_id":"70b17c47-566d-59cd-ab8b-ad20f55ac2f8","page_number":1,"excerpt":"【合成片段】本示例展示检索增强生成的来源定位，不代表真实论文或实验结论。","synthetic":true}],"insufficient_evidence":false,"mock":true}
```

五个方法的完整输入/输出及“证据不足”均在同一机器示例，不另造接口。类型/供应商对象不能透传给 B。

## 5. 异常响应与失败表达

同一 fixture 的 question.question 改成“模拟失败”，Mock answer 实际抛出以下 AppError；该字符串只用于 Mock，不要求真实模型识别特殊口令。

```json
{"status":502,"code":"AI_UNAVAILABLE","message":"模拟 AI 服务失败，可重试"}
```

C 应在 SDK 边界将超时/不可用等映射为安全 AppError（502/503/504），不输出密钥、供应商正文或内部异常。B 捕获后更新 Task.error(code,message)，创建已返回202不代表处理成功。输入不合法/越权和未知模型故障须明确区分；证据不足是结构化业务结果，不伪装网络故障。B 对无效引用拒绝 INVALID_CITATION；不符合模型 Schema 拒绝 ADAPTER_CONTRACT_ERROR。

## 6. 注册、启动及测试

C 完成后交付 `build_ai(data:DataPort) -> AIPort`，工厂闭包持有 C 自身配置和受控文件/检索依赖，B 不引入 SDK。可信组合入口调用：

```python
from app.ai.adapter import build_ai
from app.config import Settings
from app.main import create_app as create_business_app

def create_app():
    # 启动前设置 PAPERPILOT_C_MODE=real，读取统一端口/Origin 等环境配置。
    return create_business_app(Settings.from_env(), ai_factory=build_ai)
```

`main.create_app` 和 `adapters.factory.create_adapters` 已支持此参数。默认 C=mock 不接受 real factory；C=real 未注册、返回不完整端口、构造失败均拒绝启动，不回退。可以 A/D 保持 Mock 独立开发。模块初始化资源由 C/组合入口持有并负责清理；AIPort 本身没有 ready/close，生产生命周期需再与 B 对齐。

```powershell
# 先运行现成 Mock 骨架；接入 C 后用其已交付的组合模块替换 app.main
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
# 另一终端按统一指南运行前端，下列测试无需启动服务
.\.venv\Scripts\python.exe -m pytest backend/tests/ai_contracts -q
.\.venv\Scripts\python.exe scripts/export_module_examples.py --check
# C 交付隔离 provider 后替换为实际模块名，不能指向生产配置
.\.venv\Scripts\python.exe -m pytest backend/tests/ai_contracts --ai-contract-provider ai_contracts.real_provider:build_harness -q
```

provider 为 `build_harness(*,tmp_path)` context manager，返回 `ai_contracts.harness.AIHarness`。字段定义以该 dataclass 为准：实际 ai、配置故障的 failing_ai、DataPort、两个合法项目上下文、task_id、项目绑定 file、能匹配 fixture 的 query/source、failure_question、is_mock=False。provider 自备合法 PDF/可控 reader、可检索来源和故障 SDK；默认 Mock 不解析文件。测试不应依赖真实收费模型或用户库。

当前 10 项共享 C 行为覆盖五方法、三模式、年份/数量、项目范围、原始来源、空证据、只返回草稿和故障。通过不表示检索效果、PDF 质量或提示注入防护通过。启动和前端代理见 [统一开发指南](development-guide.md)，交付 C 的组合入口后运行原四页，无需修改页面。

## 7. 需要交付

AIPort 实现及 build_ai、可控测试 provider/fixture、SDK 配置示例（无真实密钥）、文件访问和索引依赖说明、正常/空/故障结果、效果评测脚本及数据出处。向 A 交付正文/片段/向量载荷需求；向 B 交付可运行组合入口和测试结果。

## 8. 验收标准

全部方法 async、当前 Schema/来源校验兼容、服务端项目范围有效、无无依据引用、明确不足/故障、不写报告版本或任务状态、real 工厂可替换Mock且无降级；共享行为及 B 集成测试通过。真实 PDF、检索效果与模型质量另提供实际数据评测，不能用 Mock 测试代替。

## 9. 不属于 C

不改前端、公开 HTTP API、AB-1；不建业务数据库/迁移，不写报告版本/任务状态，不执行模型 SQL，不配置 D 的数据库角色或安全网关。

## 10. 待团队确认及阶段边界

同进程受控本地文件读取、C 总超时/取消/模型资源清理由本次实现提供；远程文件 API、A/C 全文/向量持久化提交面与大库性能仍待团队确认。交大聊天模型与本地 BGE 嵌入可显式组合，不使用 A 的三维占位向量。C/D 若需 NL2SQL 协作另行商定私有边界，当前 AIPort 没有 generate_sql 方法。流式与跨模块租约心跳未实现，真实 PostgreSQL 联调和模型效果边界见本次交付文档。
