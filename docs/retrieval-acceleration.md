# 查询加速：版本化词频索引与常驻 BGE

本次在 [全库检索 B0](full-corpus-retrieval.md) 上实现两个可单独关闭的优化：关键词不再为每个查询读取并分词全部原文；查询嵌入不再为每个问题重新加载模型。真实文献、片段、原文页码、模型版本、文档向量和精确 SQL 余弦检索均复用现有数据。

开发分支先同步主分支 `0d94a38`，包含 A 的数据字典/演示功能和 D/B 的认证部署对齐。冻结的 B0 仍为 `1543727`；本次另跑同步后的关闭优化控制组，避免将不同时间、传输与环境差异误算为加速收益。

## 关键词索引

`app.ai.lexical_index` 按论文构建派生词频索引，标识包含项目、论文、当前处理任务、标题和分词策略。每次先在数据库只读一致性快照中读取当前授权论文版本；移出、年份/指定论文范围、重新处理和标题变化会改变实际选择或缓存标识。

未命中的论文按一条有序服务端游标读取一次，计算 TF、片段长度、词到片段的倒排表，用整数数组压缩存储。只保留派生词频与来源键，不缓存全文或权限结论。并发的首次请求共享构建；按最少使用顺序淘汰旧版本，默认每 API 进程的缓存记账预算为 256 MiB。

查询在选中论文的索引上重新计算 N、平均长度和查询词 df，使用原公式 k1=1.5、b=0.75；没有把全项目 IDF 用于所有年份范围。分词沿用 NFKC、英文词/数字、汉字单字和相邻双字，查询词去重、排序，同分按 paper/page/chunk 稳定排序。浮点贡献按原词序调用 Python `sum()`，保留补偿求和结果。

超过记账/构建预算会回退到 B0 的完整流式 BM25，绝不只排缓存中的文献。256 MiB 是索引对象的估算预算，不是进程 RSS 上限；活动请求的评分数组、构建临时空间和模型/依赖另占内存。索引不落盘，重启后的首次关键词查询需要重新构建，单独记录冷查询。

实现参考 [BM25S 预索引与倒排思路](https://github.com/xhluca/bm25s)。为保留现有公式、筛选统计和无额外依赖的部署，本次使用紧凑 TF 数组，没有引入 BM25S/LlamaIndex 默认分词与评分配置。

## 常驻查询嵌入

`LocalEmbeddings.embed_query()` 使用独立受监督进程。应用可选私有 `astart` 钩子加载已准备的 BGE 并确认 ready，`aclose` 回收进程。不扩展公开 AIPort 的五方法契约，不把 ONNX 推理放入事件循环。

查询进程串行执行，默认最多 16 条等待加 1 条执行，超出返回 `503 AI_EMBEDDING_BUSY`。等待与执行均受期限约束。取消排队者不影响执行者；取消/超时的执行者杀死并回收进程，后续请求重新启动。进程退出、响应编号错误及异常响应均失败，不把陈旧结果交给其他请求。向量仍检查维度、有限性与归一化；每完成 1000 次请求回收一次进程。

查询进程保留 3 GiB 地址空间边界，不使用会随请求累计的 CPU 秒上限；单请求由父进程墙钟期限及取消回收约束。长论文的批量嵌入继续使用原独立子进程及 180 CPU 秒上限，不与在线问题共用排队通道。

模型、revision、query/passage 预处理、token 上限及归一化不变，无需重嵌入论文。没有查询结果缓存或查询向量缓存，未见问题同样可使用预索引和常驻模型。

## 配置

```dotenv
PAPERPILOT_AI_LEXICAL_BACKEND=inverted
PAPERPILOT_AI_LEXICAL_CACHE_MB=256
PAPERPILOT_AI_QUERY_EMBEDDING_WORKER=resident
PAPERPILOT_AI_EMBEDDING_QUEUE_SIZE=16
```

将 lexical backend 设为 `streaming`、query embedding worker 设为 `oneshot` 可关闭两项优化。项目授权、模型/索引校验和返回前来源复核继续执行。没有数据库迁移、GIN/HNSW 索引或自动重处理任务。

## HTTPS 演示入口

新主分支要求密码认证使用 HTTPS。启动器和 Vite 只增加本机传输配置：两端使用操作者准备的证书，Node 代理以该证书验证后端，没有关闭证书校验、HTTPS 门槛或修改系统信任库。

在本机准备证书，私钥只保存在 `.runtime`：

```bash
umask 077
mkdir -p .runtime/acceleration/tls
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout .runtime/acceleration/tls/localhost.key \
  -out .runtime/acceleration/tls/localhost.crt -days 30 \
  -subj /CN=localhost -addext subjectAltName=DNS:localhost,IP:127.0.0.1
```

准备已有数据库配置、uploads 和模型配置后：

```bash
PYTHONPATH=backend .venv/bin/python scripts/start_research_demo.py \
  --state-dir .runtime/acceleration \
  --model-manifest /home/huyi/Codes/PaperPilot/.runtime/ai-model.json \
  --provider-config /home/huyi/.openclaw/openclaw.json \
  --api-port 18805 --web-port 15178 --chunk-strategy sentence-token-v2
```

启动器默认读取状态目录的 `tls/localhost.crt` / `localhost.key`，也接受 `--tls-cert` / `--tls-key`。本机地址为 **https://127.0.0.1:15178**，首次访问需确认本机自签名证书；账号和密码沿用已有本机文件。脚本不初始化数据库或重置密码；其他环境需替换为各自准备的路径。

## 真实文献验收

同一批 300 篇、20986 个当前来源、原 BGE 512 维向量和固定 40 条中英文问题。结果在 [demo/retrieval-acceleration](../demo/retrieval-acceleration/)，不含论文全文、密钥、私钥或 Cookie。

2026-10-31 实测 HTTPS 顺序查询中位数如下，四组分别 40 条不同问题，分模式为 10 / 10 / 20 条：

| 配置 | 关键词 | 语义 | 组合 |
|---|---:|---:|---:|
| 同代码控制组：流式 + 单次进程 | 1.925 秒 | 0.568 秒 | 2.582 秒 |
| 仅常驻查询模型 | 2.248 秒 | 0.253 秒 | 2.490 秒 |
| 仅词频倒排索引 | 0.053 秒 | 0.617 秒 | 0.588 秒 |
| 两项同时启用 | **0.043 秒** | **0.219 秒** | **0.236 秒** |

相对本轮控制组的中位数比值为 **44.9× / 2.6× / 10.9×**。固定顺序、单次每问题测量和 CPU 波动意味着不能将仅常驻组关键词的变慢解读为算法退化；关键词路径没有调用查询模型。2026-10-30 历史 HTTP B0 的 5.44 / 1.40 / 6.96 秒仍保留，未直接用于上述倍数。

组合方案首个关键词请求含索引构建，实测 **2.966 秒**；其后的单独热重复请求为 0.072 秒。该首次构建也保留在 10 条关键词样本内，使该小样本的关键词 p95 为 2.966 秒，不能只展示热查询中位数而隐藏冷启动。只启用词频索引一组首次构建为 4.074 秒。

四组 160 条顺序请求及各组 1 次热重复全部成功，完整响应摘要逐条一致；组合方案并发 2、4、8 各 40 条，共 120 条请求也全部成功且与控制组一致：

| 并发 | 本次混合工作集成功 QPS | 关键词 / 语义 / 组合 p95 |
|---|---:|---|
| 2 | 8.89 | 0.164 / 0.353 / 0.391 秒 |
| 4 | 11.85 | 0.257 / 0.481 / 0.503 秒 |
| 8 | 12.62 | 0.407 / 0.688 / 1.292 秒 |

这些是有限工作集的短探测，不是长期吞吐能力。并发 8 时吞吐增长变缓、组合延迟上升，下一步可对数据库/来源校验往返与连接池进行专门剖析。

独立实际索引检查显示 300 个论文索引记账约 **147.06 MiB**，默认 256 MiB 未触发回退。顺序测试后的 Linux RSS 快照：控制组 API 约 143 MiB；组合方案 API 约 310 MiB、查询进程约 199 MiB，两者相加约 509 MiB。RSS 可能重复计共享页，是进程快照而非独占内存或整个系统峰值。组合方案应用启动约 1.03 秒，控制组约 0.64 秒；模型启动开销已移到启动阶段。

30 条真实 BGE 查询向量的新旧浮点数完全相同（最大绝对误差 0），强制退出重启、批量通道独立与关闭回收均通过。全部文献来源前后快照摘要一致。

- [embedding-check.json](../demo/retrieval-acceleration/embedding-check.json)：30 条真实 BGE 查询向量逐条比较，以及进程复用、退出重启、关闭回收、批量通道独立检查。
- [benchmark.json](../demo/retrieval-acceleration/benchmark.json)：同代码、同 HTTPS/认证、同单 API worker 的四组对照，含来源核验、首次构建、热查询、启动时间和并发 2/4/8。历史 HTTP B0 单列，不直接用它计算本次收益。

逐条比较完整 SearchResult（含排序、分数、论文和来源）的 SHA256，另核对原文页码、摘录、项目归属及 synthetic。每组运行 40 个不同问题，首条包含索引构建，另记热重复问题。来源快照前后摘要必须一致。

```bash
PYTHONPATH=backend .venv/bin/python scripts/verify_resident_embeddings.py \
  --model-manifest /home/huyi/Codes/PaperPilot/.runtime/ai-model.json

PYTHONPATH=backend .venv/bin/python scripts/benchmark_retrieval_acceleration.py \
  --database-config .runtime/acceleration/database.json \
  --import-state /home/huyi/Codes/PaperPilot-chunking/.runtime/chunking-v2/import-state.json \
  --upload-dir .runtime/acceleration/uploads \
  --model-manifest /home/huyi/Codes/PaperPilot/.runtime/ai-model.json \
  --tls-cert .runtime/acceleration/tls/localhost.crt \
  --tls-key .runtime/acceleration/tls/localhost.key \
  --password-file /home/huyi/Codes/PaperPilot/.runtime/real-demo/secrets/alice-password.txt
```

程序顺序启动并关闭自己拥有的测试 API，默认端口 18806，拒绝接管占用端口。数据库与 OS 缓存不清空，固定顺序及有限样本的影响记录在结果中。延迟统计只计成功请求，错误单列；并发探测不是长期容量或 SLA。没有人工相关性标注，不报告 nDCG 或答案质量提升。

## 自动检查

覆盖按范围计算 BM25、浮点求和、项目/标题/处理版本失效、容量回退、LRU 淘汰和并发首次构建；常驻进程覆盖排队上限、等待者取消、执行者取消/超时、异常退出、响应及向量校验、启动失败清理。临时 PostgreSQL 回归在重切块前预热旧词频索引，验证之后只返回新片段，历史来源仍保留。

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q

# 配置已有的临时库测试管理员；测试库自动清理。
PAPERPILOT_RETRIEVAL_POSTGRES_TEST=1 PAPERPILOT_CHUNKING_POSTGRES_TEST=1 \
  PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_indexed_retrieval_postgres.py \
  backend/tests/test_sentence_chunking_postgres.py -q
```

浏览器另核验 HTTPS 登录、全项目组合检索和原文跳转。本机仍使用既有开发业务角色，不把查询测试当作全部生产 RLS 或完整安全验收。
