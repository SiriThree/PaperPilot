# 全库检索：解除 1000 片段门槛

旧版 C 先枚举项目的全部来源，再逐片段向 A 读取向量，最后在 Python 排序；枚举超过 1000 个片段就返回 `503 AI_CORPUS_LIMIT`。句段切块后的真实 300 篇共有 **20986 个片段**，因此三种全库检索均被拒绝。

新版 PostgreSQL 路径让全部授权片段参与检索，将 1000 改为**混合检索每一路排名靠前的候选数**。单独的关键词、语义模式只保留请求的 Top-K；混合模式分别保留默认 1000 个 BM25 候选和 1000 个语义候选，再做 RRF，最终最多返回公开契约允许的 50 条来源。

## 实现和边界

| 模式 | 参与计算的范围 | 排序和应用内存 |
|---|---|---|
| 关键词 | 全部符合项目、年份、指定论文条件的当前来源 | 两遍服务端游标，BM25 全库统计与评分，大小为 K 的堆 |
| 语义 | 全部符合上述条件且模型索引匹配的当前来源 | pgvector 精确余弦排序，SQL Top-K，仅返回键和分数 |
| 组合 | 上述两个全库计算过程 | 每路最多 `max(max_candidates, query.limit)` 条，再 RRF |

关键词规则沿用 NFKC、英文词和数字、汉字单字及相邻双字。两遍扫描在同一个 `REPEATABLE READ, READ ONLY` 事务中执行；第一遍得到文档数、平均长度和查询词文档频率，第二遍计算原 BM25 公式并保留 Top-K。采用 [Psycopg 服务端游标](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#server-side-cursors)，每批 256 行，避免把所有原文、词频表和分数常驻应用内存。它仍需要扫描并分词全文，CPU 与传输开销随片段数增长。

语义检索只嵌入查询，匹配完整的 provider、model_name、revision、dimensions、distance_metric。先检查全部当前来源的索引状态和对应向量，任何缺失或模型不匹配均返回 `409 INDEX_NOT_READY`，不会把缺失向量的论文悄悄排除。随后用 [pgvector 的余弦距离运算](https://github.com/pgvector/pgvector#querying)执行精确 Top-K，避免旧 `indexed_vectors` 的逐片段查询和全部向量传输。

查询计划检查发现，将模型元数据、处理索引和来源全部放入一个连接查询时，估计基数偏差使 300 篇的来源子计划重复执行 300 次。实现改为在同一快照内先解析授权项目的模型 ID、检查索引，再用该 ID 连接向量排序。没有修改表结构、安装额外服务或建立 ANN 索引。

候选键为 `(paper_id, page_number, chunk_id)`，分数相同按此键稳定排序。最终只批量读取获选来源及论文，重编本次 citation_id。事务结束后 C 再验证当前权限和来源，B 原有的契约与来源校验继续执行。模型调用不占用数据库事务；数据库语句与流式扫描有剩余时间预算，超时返回 `504 AI_TIMEOUT` 并回滚。

## A/C 对接

- C 算法在 `app.ai.retrieval.bm25_topk`、`RealAI._search_indexed` / `_rank_indexed`。
- A 提供私有 `PostgresData.retrieval_snapshot`，其读取实现集中在 `app.data.retrieval`；连接池事务增加只读快照选项。
- AIPort 的 5 个方法、DataPort 的公开方法和 HTTP Schema 保持原契约。旧来源、片段 ID、页码、处理任务及向量均复用，不需要重新切块或重新嵌入。
- 未实现该私有扩展的隔离/其他 DataPort 继续使用原来的受限路径，仍可能返回 `AI_CORPUS_LIMIT`；不会自动切换到未授权的数据库连接。

这次只交付 PostgreSQL 全库检索路径。关键词仍为全库流式扫描，语义仍为精确扫描；更大规模的倒排索引、缓存、HNSW 和常驻嵌入进程需分别测量后再实施。混合模式的两路候选截断可能改变相较“所有排名全部做 RRF”的结果，因此不宣称它与无限候选 RRF 等价。

## 真实文献验收

使用同一份 [300 篇句段切块目录](../demo/chunking-v2/catalog.json) 和 [40 条固定中英文输入](../demo/rag-300/workload.json)。验证脚本使用真实 BGE 查询嵌入及 PostgreSQL 中的原有 512 维文档向量，不调用聊天模型。

结果保存在 [verification.json](../demo/retrieval-v2/verification.json)：记录逐次 HTTP 状态、命中数、耗时、来源核验和代码文件 SHA256。优化前错误与成功耗时分别记录，错误不会参与速度比较。来源全文与密钥不进入结果文件。

2026-10-30 本机实测：旧版同项目三种模式均返回 `503 AI_CORPUS_LIMIT`；新版固定 **40/40** 条查询成功，每条返回 10 个真实来源，**6/6** 次并发探测成功。全部 20986 条当前来源的前后摘要一致。

| 模式 | 顺序查询数 | HTTP 耗时中位数 | 最小–最大 |
|---|---:|---:|---:|
| 关键词 | 10 | 5.44 秒 | 5.24–6.01 秒 |
| 语义 | 10 | 1.40 秒 | 1.20–1.50 秒 |
| 组合 | 20 | 6.96 秒 | 6.64–7.70 秒 |

并发 3 的六次小探测中，关键词为 15.12–16.25 秒、语义为 2.55–3.93 秒、组合为 7.01–18.78 秒。关键词分词和评分仍在 Python 线程中执行，并发时 CPU/GIL 竞争是后续应专门分析的瓶颈；顺序中位数不能当成并发延迟承诺。

脚本还独立核对两项排序：

1. 以原 `bm25()` 对全库评分，和流式实现的 Top-50 ID、分数逐项一致。
2. 选一个现存真实 BGE 文档向量作为精确检索探针，以 Python 流式余弦计算作参考，核对 SQL Top-50 ID 及分数误差不超过 `1e-6`。实际 HTTP 的所有语义/组合查询仍分别调用真实查询嵌入。

每次 HTTP 搜索均核对返回来源的论文归属、页码、摘录、片段 ID、synthetic 标记和结果编号；验收前后对全部当前来源计算摘要，确保使用的是同一文献快照。

```bash
PYTHONPATH=backend .venv/bin/python scripts/verify_full_corpus_search.py \
  --api-base http://127.0.0.1:18804 --origin http://127.0.0.1:15177 \
  --database-config .runtime/retrieval/database.json \
  --import-state /home/huyi/Codes/PaperPilot-chunking/.runtime/chunking-v2/import-state.json \
  --password-file /home/huyi/Codes/PaperPilot/.runtime/real-demo/secrets/alice-password.txt \
  --baseline-api http://127.0.0.1:18803 --baseline-origin http://127.0.0.1:15176
```

`--baseline-api` / `--baseline-origin` 可省略；若使用，需先启动优化前的服务并指向同一文献项目。其余路径替换为操作者已准备的本机配置；脚本不安装依赖、不导入数据、不重置密码。数据库读取使用现有业务角色；本机演示配置不等于生产 RLS 安全验收。

记录的模式耗时来自单机单次顺序执行，不是容量指标。另有 6 次、默认并发 3 的小规模探测，只验证同时请求能否成功，不代表完成长时间压力测试。当前工作集没有人工相关性标注，因此不报告 Recall、nDCG 或答案质量提升。

## 自动检查

`test_streaming_bm25.py` 用超过 1000 个协议片段验证关键词公式、稳定顺序和中英文规则等价；`test_indexed_retrieval_postgres.py` 显式启用后创建并清理临时数据库，覆盖超过旧门槛、排名前过滤项目/年份、空范围、模型/维度不匹配、当前版本、数据库快照、权限撤销和超时恢复。协议测试数据只用于边界验证，真实文献结果以上述 300 篇文件为准。

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_streaming_bm25.py -q

# 配置 PAPERPILOT_TEST_DATA_HOST/PORT/USER/PASSWORD，指向可创建临时库的本机测试管理员。
# 密码使用本机环境变量，不写入命令记录或仓库。
PAPERPILOT_RETRIEVAL_POSTGRES_TEST=1 PYTHONPATH=backend \
  .venv/bin/python -m pytest backend/tests/test_indexed_retrieval_postgres.py -q
```
