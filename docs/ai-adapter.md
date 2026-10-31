# 成员 C：真实 PDF、检索与交大模型接入

C 实现位于 `backend/app/ai/`，交付五个 async AIPort 方法与 `build_ai(data: DataPort) -> AIPort`。公开 Schema、HTTP API、AB-1、前端及任务/报告持久化保持原有边界。默认启动仍为 Mock，真实 C 必须显式注册；配置缺失、解析失败、供应商失败均不回退。

开发基线为 B 的 PR #3 已合入后的 `main@4770732`。同步检查中，A 的 PR #1（`member-a@808ced9`）和 D 的 PR #2 仍为草稿；C 不提前合入或修改同学的实现。已对 C 提交与当前 A 分支执行三方合并检查，无文本冲突；这不等同于真实 PostgreSQL 联调。合并前应重新 fetch 核对。

## 安装与启动

完整开发依赖沿用 `requirements.lock`（增加 PDF 解析器）；只安装真实 C 可使用 `pip install -e '.[ai]'`，需要本地嵌入则使用 `pip install -e '.[ai-local]'`。后者额外安装 FastEmbed 0.9.1；默认 Mock 不导入它。

以下为 Linux/macOS 示例；Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`，用 `$env:NAME='value'` 设置环境变量。进程不会自动读取 `.env`。

```bash
.venv/bin/python -m pip install -e '.[dev,ai-local]'
# 显式下载本地模型，之后应用运行不联网下载模型。
.venv/bin/python scripts/prepare_ai_embeddings.py
# 输出 .runtime/ai-model.json，包含实际模型目录、维度与逐文件 SHA256。
export PAPERPILOT_ENV=development
export PAPERPILOT_A_MODE=mock
export PAPERPILOT_C_MODE=real
export PAPERPILOT_D_MODE=mock
export PAPERPILOT_AI_BASE_URL=https://models.sjtu.edu.cn/api/v1
export PAPERPILOT_AI_CHAT_MODEL=deepseek-chat
# 将本人申请的 key 安全注入 PAPERPILOT_AI_API_KEY；不要写进命令历史或提交。
export PAPERPILOT_AI_EMBEDDING_BACKEND=fastembed
export PAPERPILOT_AI_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
export PAPERPILOT_AI_EMBEDDING_DIMENSIONS=512
# 替换为 prepare_ai_embeddings.py 输出的 model_dir（不是缓存的上级目录）。
export PAPERPILOT_AI_LOCAL_MODEL_DIR=/absolute/path/to/prepared/model
.venv/bin/python -m uvicorn app.ai.bootstrap:create_app --factory --app-dir backend \
  --host 127.0.0.1 --port 8000 --no-access-log
```

前端仍按统一指南启动四个页面。可以在新项目上传真实文本 PDF、检索、生成回答/证据/草稿，再由原保存接口创建报告版本。页面中 B 的 Mock 提示文案属于 B 的既有页面状态，并不由 C 修改；查看响应中的 `mock`、`synthetic` 与健康模式核实实际来源。A=mock 时，新内容仍随内存数据重启丢失。

## 交大服务核对

官方资料：[本地大模型 API 使用文档](https://claw.sjtu.edu.cn/guide/sjtu-api/)。仅校园网/VPN 可访问；在交我办申请 API Key。2026-10-27 实际 GET `/models` 成功，返回 `qwen`、`claw`、`deepseek-chat`、`deepseek-reasoner`、`qwen3.8-27b`。模型目录会变化，以自己的实时列表为准。用 `deepseek-chat` 进行两个极小探测，`json_schema/strict` 与 `json_object` 均返回预期 JSON；完整真实样例结果另见评测记录。

官方资料与当前目录没有列出嵌入模型，不能据此宣称交大提供嵌入 API。本次采用交大生成 + 显式配置的本地 BGE 嵌入。官方目前标注 10 requests/min；评测器默认每次供应商调用间隔 7 秒。适配器遇到 429 返回 `AI_UNAVAILABLE`，不自行重试、换供应商或降级。

## 配置

| 变量 | 含义 |
| --- | --- |
| `PAPERPILOT_AI_BASE_URL` / `PAPERPILOT_AI_API_KEY` | 聊天服务地址及密钥，必填；HTTPS 或回环 HTTP，不允许 URL 内凭据 |
| `PAPERPILOT_AI_CHAT_MODEL` | 支持 Chat Completions 严格 JSON Schema 的实际模型调用名，必填 |
| `PAPERPILOT_AI_EMBEDDING_BACKEND` | `remote`（默认）或 `fastembed`，无自动切换 |
| `PAPERPILOT_AI_EMBEDDING_MODEL` / `..._DIMENSIONS` | 实际嵌入模型及期望维度，必填；返回向量必须同维、有限、非零，不做隐式投影 |
| `PAPERPILOT_AI_EMBEDDING_BASE_URL` / `..._API_KEY` | 可选独立嵌入服务，两者必须一起配置；remote 模式均未指定时才使用聊天服务地址/密钥，不把聊天密钥自动发送给另一服务 |
| `PAPERPILOT_AI_LOCAL_MODEL_DIR` | fastembed 必填；显式准备好的模型目录，缺少依赖/文件或维度不符拒绝启动 |
| `PAPERPILOT_AI_TIMEOUT_SECONDS` | 每个 AIPort 操作总期限，默认 45，范围 (0,90] 秒，包括所有嵌入批次；需小于 A 的租约 |
| `PAPERPILOT_AI_CHUNK_STRATEGY` | `auto`（默认）：已准备的 fastembed BGE-small-zh-v1.5 使用 `sentence-token-v2`，其他模型/remote 使用 `character-v1`；可显式选择旧版复现基线 |
| `PAPERPILOT_AI_CHUNK_TOKENS` / `..._TOKEN_OVERLAP` | V2 正文 token 目标 / 重叠上限，默认 384 / 48；标题、正文、特殊 token 合计仍须符合模型输入上限 |
| `PAPERPILOT_AI_MIN_SIMILARITY` | 余弦阈值，默认 0.2；是开发阈值，需要按语料校准 |
| `PAPERPILOT_AI_TOKEN_LIMIT_PARAMETER` | 默认 `max_tokens`（交大示例）；可明确改成 `max_completion_tokens` |
| `PAPERPILOT_AI_RESPONSE_FORMAT` | 默认 `json_schema`；DeepSeek 官方 Chat Completions 可显式选 `json_object`。客户端将 JSON Schema 写入系统提示并保留本地严格 Pydantic 校验及引用校验；不自动切换或重试。 |
| `PAPERPILOT_STORAGE_DIR` | 与 B 共用受控本地上传目录，默认 `.runtime/uploads` |

DeepSeek 官方服务与上述交大历史示例是不同服务；官方地址为 `https://api.deepseek.com`，当前模型标识请以官方 API 文档及账号权限为准。选择 `json_object` 不等于服务端严格保证 Schema；无效结构、非法引用和不支持的状态仍会被拒绝。真实服务、模型额度及嵌入服务须单独确认；未测试前不能声称兼容验收已通过。应用不会自动加载 `.env`。

生成采用 HTTPX，无供应商 SDK 类型外泄。每次 HTTP 调用都用上下文管理器关闭连接，取消会传播；不保留全局客户端，不自动跟随重定向，不隐式读取代理变量。超时/连接故障分别映射安全 AppError 504/503；拒绝请求为 502；无效 JSON、截断、拒答、向量异常为 `AI_INVALID_RESPONSE`。不记录供应商正文、密钥或论文文本。

本地模型来自 [Qdrant 的 FastEmbed 模型目录](https://qdrant.github.io/fastembed/examples/Supported_Models/)，模型卡：[BGE small Chinese v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)。默认 512 维、CPU 两线程；原始模型有 512-token 截断，分词后超长片段的后部可能不参与语义表示。通过 FastEmbed 的 query/passage 方法生成向量并归一化；模型及分词版本、文件哈希应随评测保留。推理在可终止的独立子进程中运行；请求超时或取消会终止并等待子进程，不遗留后台推理。

本地推理子进程在 POSIX 下限制地址空间为 3 GiB、CPU 时间为 180 秒；CPU 时间会累计两个线程的用量，与墙钟时间不同。外层 AIPort 操作期限仍最多 90 秒，取消时终止子进程。旧的 60 CPU 秒限制在真实 379 分片论文上约 31 墙钟秒即触发，因此调整为与两线程最大操作期限一致。非零进程退出报告 `AI_EMBEDDING_UNAVAILABLE`，与有效进程返回畸形 JSON 的 `AI_INVALID_RESPONSE` 区分。

## 文件、片段与检索

`LocalPDFReader` 只接受 B 的 `<paper_id>.pdf` 存储键；先检查 Context、项目成员和论文，再读取可信上传根目录下的普通文件，拒绝路径、URL 和符号链接。无需暴露上传目录或创建下载 HTTP 接口。这已解决同进程本地文件读取；跨主机文件传输/存储服务不在本次交付内，可通过 reader 私有依赖替换。

PDF 使用 pypdf 独立子进程解析，不跨页分片。本机 BGE 默认 V2：按段落、句子、子句、词边界打包，正文最多 384 token、重叠最多 48 token，并用实际模型 tokenizer 检查“标题 + 换行 + 正文 + 特殊 token”没有超限。句子检测使用固定版本 PySBD，不下载句子模型；PDF 单换行仅在等长的边界检测视图中暂换成空格，存储摘录仍是原文切片。普通词能够完整放入下一块时，宁可本块短一些；超长不可分 URL/公式等才使用有记录的回退。

remote 默认及显式 `character-v1` 沿用 1200 字符、重叠 120 字符。页码从 1 起，空白页不移动后续页码。V1 的 chunk ID 算法保持兼容；V2 将策略版本、PySBD 版本、tokenizer 文件哈希、预算、页码、原文偏移和文本摘要纳入 ID，重试稳定且与 V1 不混用。返回原提取文本，`mock=false`、`synthetic=false`；这表示从 PDF 提取，不保证 PDF 内陈述真实。已存合成来源在检索和生成时保留 `synthetic=true`，输出另加合成提示。真实 300 篇对照、重建与限制见 [V2 切块说明](sentence-token-chunking.md)。

V2 本地嵌入在推理前逐条检查最终输入，超过模型上限返回 `AI_EMBEDDING_INPUT_LIMIT`，避免静默右截断；此规则也适用于超长检索问题。V1 保留原有行为用于复现。切块策略变更需要显式重处理或导入到独立对照库，不会在检索时改写已有向量及历史引用。

上限：20 MiB、300 页、200 万提取字符、2500 片段；加密、无文本扫描件、损坏、超限均显式失败，不伪造 OCR。解析子进程在 POSIX 下限制内存/CPU，所有平台均受操作期限约束。多栏阅读顺序、公式、表格、图像、OCR 与精确坐标尚未专门解决。

检索在服务端按当前项目、年份和指定论文范围建立候选集，仅接受 searchable 论文。PostgreSQL 私有路径在只读一致性快照中筛选当前处理版本；其他 DataPort 沿用 `papers` / `sources` 路径。关键词为中英文 BM25；语义为真实嵌入余弦排序；混合为两路 RRF（常数 60）。结果编号重新分配、原始摘录/页码/合成标记不变。调用模型后再检查成员、论文和来源，删除/撤权后不能返回旧缓存结果。

持久化 A+C 使用 PostgreSQL 版本化文档向量，检索只嵌入问题。默认按当前论文版本复用词频倒排索引，查询 BGE 在独立常驻进程推理；可用配置切回流式 BM25 / 单次子进程。SQL 精确余弦 Top-K 与两路 RRF 保留，默认 1000 是组合模式每路的候选数。无私有读取扩展的其他 DataPort 保留旧预算；无持久化向量扩展时仍每次嵌入文档。见 [全库检索说明](full-corpus-retrieval.md) 与 [加速及对照验收](retrieval-acceleration.md)，当前没有 ANN 索引。

## 来源、生成与不足

只使用调用方提供的授权来源，先逐项核对 canonical chunk/page/excerpt/synthetic，再向模型发送来源数据；最多取排序前 50 个、累计不超过 60000 字符，不改写摘录。模型只返回文字、状态与 citation_ids，不能生成 public Source。引用不在本次来源内、重复编号、正文自行夹带方括号引用、无依据成功、状态矛盾均拒绝。

空来源时直接返回结构化不足，不调用模型。answer 的正文引用和 citation_ids 由程序渲染并与返回来源一致。问答/草稿与五字段提取使用独立输出协议，提取提示不再包含问答的 `statements`/布尔不足格式。提取明确要求简短中文正文，以及字符串形式的来源编号。

extract 的五字段只允许有文本、有来源的 `pending_verification`；其他状态必须为 null/无引用。这一状态关系在私有模型解析时校验，随后仍验证引用是否属于当前授权原文。缺字段、截断、类型错误、状态矛盾或伪造引用都失败，不自动补字段、丢弃异常正文或隐式重试。draft 只返回待核验正文，不写任务、证据或报告版本。合成输入不重标真实。

提示将问题和论文正文视为不可信数据，不提供工具、SQL、文件执行或外部检索权限。结构校验只能保证来源身份和引用范围，不能证明每个生成陈述被来源蕴含，亦不代表完成了提示注入安全认证；生成内容仍需人工核验。

## A/B 交付边界

A 继续拥有任务及结果提交。合并后 B 在真实 A/C 均支持时调用私有 `process_indexed` / `complete_indexed_job`，在一个 process attempt 内原子提交完整文本块、模型、向量、来源和 ready；五方法 AIPort/26 方法 DataPort/HTTP Schema 不变。C 不直接写数据库、领取或完成任务；历史 `EMBEDDING_DIM=3` 不控制实际向量。

持久化组合必须显式配置 `PAPERPILOT_AI_EMBEDDING_REVISION`，同模型版本不能改变维度，切模型需明确重建；不能混用占位三维向量与 BGE 512 维。历史无对应版本索引的论文返回 `INDEX_NOT_READY`，关键词检索仍可用；单篇明确重建及完整约束见[持久化索引说明](persistent-index.md)。

合并后可在团队组合模块中显式组合（持久化配置还须设置模型 revision）：

```python
from app.ai.bootstrap import create_app as create_c_app
from app.config import Settings
from app.data.binding import compose_data_binding
from app.data.database import ConnectionConfig
from app.data.settings import DataSettings

def create_app():
    binding = compose_data_binding(ConnectionConfig.from_env(), DataSettings.from_env())
    try:
        return create_c_app(Settings.from_env(), data_binding=binding)
    except BaseException:
        binding.close()
        raise
```

需先按 A 文档准备数据库，设 A=persistent、C=real。正常关闭由 B 的生命周期清理 DataBinding；C 的 HTTP/PDF/本地嵌入资源在每次调用完成或取消时释放。合并后的 PostgreSQL/浏览器联调记录见[集成记录](member-d-merged-integration.md)，模型端点使用本地可控替身，不代表外部模型效果验收。

当前 B 的问答/草稿任务通过私有 `search_scoped` 先限定 paper_ids，再排序并截取最多 50 条；单篇证据任务直接读取该论文来源并应用上下文预算。公开 AIPort、DataPort 与 HTTP 契约保持不变。真实文献联调与后续格式修复记录见[演示验收](real-literature-acceptance.md)。

## 验证与真实评测

```bash
.venv/bin/python -m pytest backend/tests/ai_contracts \
  --ai-contract-provider ai_contracts.real_provider:build_harness -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check backend scripts
.venv/bin/python scripts/export_contracts.py --check
.venv/bin/python scripts/export_module_examples.py --check
```

provider 使用真实 C、真实合法 PDF、隔离 DataPort 和 HTTP 模型替身，绝不访问用户库或收费服务。`is_mock=false` 描述被测适配器；**不表示测试替身是实际模型，也不代表效果通过**。独立测试还覆盖伪造片段、跨项目文件、撤权、故障脱敏、非法引用、空证据，以及 B 的上传→处理→检索→问答/证据/草稿→保存流程。

实际公开论文评测、数据出处、重现命令、模型质量边界见 [评测说明](ai-evaluation/README.md)，机器结果位于同目录。外部论文全文、模型权重、真实生成原文与任何密钥均不纳入仓库。
