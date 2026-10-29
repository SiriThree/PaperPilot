# 句段边界与 token 预算切块 V2

V2 修复了按固定字符切块时的英文词内边界，并将送入嵌入模型的完整输入限制在模型实际 token 上限以内。公开 AIPort、DataPort 和 HTTP Source 结构保持兼容；真实全文、模型和凭据留在本机。

原 300 篇基线保持 `rag300-v1`：19659 个片段，实际 BGE tokenizer 计数发现 551 个“标题 + 正文”输入超过 512 token（2.80%）。数据库摘录没有丢失，但这些向量的输入会被原 tokenizer 向右截断。

对相同 300 篇真实 PDF 的逐页检查已完成，结果见 [`comparison.json`](../demo/chunking-v2/comparison.json)：

| 指标 | 字符块 V1 | 句段/token 块 V2 |
|---|---:|---:|
| 块数 | 19659 | 20986（+6.75%） |
| 最终输入超过 512 token | 551 | 0 |
| 输入 token P95 / 最大值 | 471 / 1074 | 399 / 422 |
| 首边界落在相邻英文字母之间 | 8942 | 0 |
| 尾边界落在相邻英文字母之间 | 8833 | 0 |
| 显式强制切分回退 | 不单独记录 | 0 |

首/尾指标按相邻 ASCII 字母检测，是边界位置数，不是不同单词数。V2 全部摘录与规范化原文偏移一致，非空白字符遗漏为 0。没有测量人工相关性或答案准确率。

## 实现选择

采用类似 [LlamaIndex SentenceSplitter](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/sentence.py) 的句段优先思想，使用 [PySBD](https://github.com/nipunsadvilkar/pySBD) 0.3.4 获取句子边界，使用已准备模型的 Hugging Face tokenizer 计数。没有引入整个 RAG 框架、调用生成模型切块，或在线下载句子模型。

每页文本经过已有 Unicode/NUL 规范化后：

1. 检测段落和句子边界，兼顾英文缩写、小数及中文 `。！？`。单个 PDF 换行不单独认作句末；检测视图的长度与原文一致。
2. 正文 token 目标为 384，优先在可用窗口后半段的段落、句子、子句或空格边界结束。若完整词需移到下一块，可接受更短的块。只有不可分长串才在 tokenizer 原文偏移处回退，并记录 `forced_split`。
3. 重叠目标上限为 48 token，优先取完整句子，其次取完整词；必要时少重叠或不重叠，避免只包含上块重叠部分的新块。
4. 对实际的 `论文标题 + 换行 + 摘录` 连同特殊 token 逐块计数，硬性保证不超过 tokenizer 配置的上下文上限；BGE 当前为 512。每段摘录仍不超过 4000 字符。
5. 摘录由原文本的字符偏移直接截取，不从 token ID 解码重建原文。保持单页 Source 和真实页码，重叠之外所有非空白原文都必须被覆盖。

PySBD 是规则边界检测器，不能据此保证每个学术缩写和句段都判断正确。当前仍由 pypdf 提取文本，双栏阅读顺序、表格结构和公式版面尚未升级。不能用较好的边界统计推断检索准确率提升。

## 配置和旧版复现

安装 `requirements-ai-local.lock` 或项目 `ai-local` extra，离线模型目录需包含 `tokenizer.json`、`tokenizer_config.json` 和模型已有特殊 token 配置。

```bash
export PAPERPILOT_AI_EMBEDDING_BACKEND=fastembed
export PAPERPILOT_AI_CHUNK_STRATEGY=sentence-token-v2
export PAPERPILOT_AI_CHUNK_TOKENS=384
export PAPERPILOT_AI_CHUNK_TOKEN_OVERLAP=48
```

`auto` 对 fastembed 选择 V2，对 remote 选择旧字符策略。remote 的具体 tokenizer 未准备前不自动假定其输入预算。显式设置 `character-v1` 可复现旧 1200 字符 / 120 字符重叠行为，已有 V1 chunk ID 算法不变。

V2 本地嵌入会在启动推理前拒绝超过上限的输入（`AI_EMBEDDING_INPUT_LIMIT`），也覆盖超长检索问题。标题耗尽预算或原文单元无法合法分割时处理失败，不偷偷截标题。现有 PDF 20 MiB、300 页、200 万字符、2500 块限制、768 MiB 地址空间 / 30 CPU 秒限制及外层操作期限继续生效。

## 版本和历史引用

V2 的 policy ID 包含切块算法版本、PySBD 版本、tokenizer 三个配置文件的组合哈希、token/重叠/字符预算；片段 ID 还包含论文、页码、原文偏移及摘录哈希。后续嵌入索引仍包含模型版本和论文标题。因此同一任务重试稳定，不同切块策略得到不同 ID。

A 的既有 `reindex_paper` 私有运维入口可以创建新的 process 任务；同模型重切块可以继续使用相同 embedding_models 记录，新索引提交后才切换 active_process_task_id。隔离 PostgreSQL 测试验证重复提交不新增索引、旧 chunks / task_results 保留不变。此版本没有新增公共重处理 HTTP 接口。

本机对照使用新数据库 `paperpilot_chunking_v2`，旧数据库 `paperpilot_corpus300` 和 `demo/rag-300/` 保留。两套服务不能共享任务库并指向不同上传目录，否则工作进程会拿到另一套文件任务。

## 真实 300 篇复现

```bash
cd /home/huyi/Codes/PaperPilot-chunking
export PYTHONPATH="$PWD/backend"

.venv/bin/python scripts/compare_pdf_chunking.py \
  --pdf-dir /home/huyi/Codes/PaperPilot-corpus300/.runtime/corpus300/papers \
  --tokenizer-dir /home/huyi/Codes/PaperPilot/.runtime/ai-models/models--Qdrant--bge-small-zh-v1.5/snapshots/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59
```

每个 PDF 在有资源上限的独立子进程中检查固定 SHA256，按同一份规范化原文生成 V1 和 V2。验证旧版块数与冻结清单一致、V2 逐块摘录与原文位置完全一致、非空白覆盖完整、最终 token 没有超限。逐块原文只保存于 `.runtime/chunking-v2/parsed/`，缓存目录包含实现和策略摘要；公开结果只保存元数据和统计。

产物为 `demo/chunking-v2/catalog.json` 与 `comparison.json`，不会覆盖 `demo/rag-300/`。脚本支持 `--target-tokens`、`--overlap-tokens`、`--limit`，比较不同配置时应选择不同输出目录，避免把试验结果混为同一语料版本。

准备独立数据库、账号和配置后，用本分支启动服务：

```bash
.venv/bin/python scripts/start_research_demo.py \
  --state-dir "$PWD/.runtime/chunking-v2" \
  --model-manifest /home/huyi/Codes/PaperPilot/.runtime/ai-model.json \
  --provider-config "$HOME/.openclaw/openclaw.json" \
  --api-port 18803 --web-port 15176 --chunk-strategy sentence-token-v2

.venv/bin/python scripts/ingest_research_corpus.py \
  --catalog demo/chunking-v2/catalog.json \
  --pdf-dir /home/huyi/Codes/PaperPilot-corpus300/.runtime/corpus300/papers \
  --api-base http://127.0.0.1:18803 --origin http://127.0.0.1:15176 \
  --password-file /home/huyi/Codes/PaperPilot/.runtime/real-demo/secrets/alice-password.txt \
  --state .runtime/chunking-v2/import-state.json --watch --max-pending 8 \
  --serialize-above-chunks 180
```

入库准备期间可临时设 `--api-workers 8`，结束后恢复默认单进程界面。导入器可用 `--serialize-above-chunks 180` 让较长论文等待其他任务完成后单独处理，避免并行 CPU 竞争挤占其操作期限；默认 0 不启用此调度，旧命令行为保持兼容。本机登录仍为 alice，密码沿用现有本机文件。服务地址是 `http://127.0.0.1:15176`。

## 检查范围

单元/子进程检查覆盖句子缩写与小数、PDF 换行、词边界、原文覆盖、重叠、CJK、超长 URL、最终标题预算、超过块数上限、重试稳定性与旧版 ID 隔离；临时 PostgreSQL 检查同模型重切块后的原子切换及历史保留。

当前全项目检索仍有 1000 候选片段上限；本次没有将该保护调大或把失败响应计作性能结果。新分片数量、边界改善和 token 覆盖不能代替 Recall@K、nDCG 或答案质量。正式效果比较要在固定模型、合法检索范围和人工证据标注下完成；搜索性能仍需分别测量数据库取候选、向量读取、查询嵌入及排序。
