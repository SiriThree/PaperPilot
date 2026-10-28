# C 的实际评测记录

2026-10-27，在 Python 3.13.2、pypdf 6.21.3、FastEmbed 0.9.1 的独立环境执行。依赖兼容范围仍为 Python ≥3.12；此次没有单独跑 Python 3.12。可选本地嵌入依赖快照为根目录 `requirements-ai-local.lock`。本记录区分真实论文/算法/模型评测与无外部调用的契约测试。

## 数据出处与范围

数据为公开论文 [Attention Is All You Need, arXiv:1706.03762v7](https://arxiv.org/abs/1706.03762v7)，[PDF 下载地址](https://arxiv.org/pdf/1706.03762v7)。15 页，SHA256 为 `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`。全文只保存在执行环境，不随 PR 重新分发。

`dataset.json` 中的 8 个相关查询及物理页码标签，在首次运行前由执行 agent 对照同一 PDF 编写；另有 1 个无关查询。标签未穷尽所有相关页。这是单论文开发集，包含明显关键词，未经独立标注/留出，不能证明跨论文、中文语料、多栏/扫描 PDF 或大库效果。数据文件自身的 SHA256 也写入结果，输入 PDF 版本不同即拒绝评测。

## 实际结果

真实 PDF 解析得到 **40 个片段，覆盖全部 15 页，最大 1200 字符，chunk_id 无重复**。解析不调用 LLM，保留页码。下表统计 8 个有标签查询，k=3 是片段数；页召回按去重页码计算。

| 模式 | Hit@3 | MRR@3 | 平均页召回@3 |
| --- | ---: | ---: | ---: |
| 关键词 BM25 | 1.0000 | 1.0000 | 0.8750 |
| 本地 BGE 语义 | 1.0000 | 0.9375 | 0.9375 |
| BM25 + BGE RRF | 1.0000 | 1.0000 | 0.9375 |

机器结果：`keyword-results.json` 为完全无模型调用的独立运行；`sjtu-results.json` 为交大生成 + 本地 BGE 的实际运行。模型来源和逐文件摘要见 `local-model.json`，其中不含用户路径、密钥或权重。模型为 `BAAI/bge-small-zh-v1.5`，ONNX 来自 Qdrant 维护版本，实际 512 维；运行时使用真实 CPU 推理，没有用替身向量计算本表。

无关查询：BM25 正确返回空；语义与混合在默认阈值 0.2 下均返回 3 条无关片段。**当前语义阈值未校准，不能宣称具备可靠的无答案识别能力。** 结果中的 `correct_abstention=false` 保留此问题；不通过删除该样例或只展示相关查询掩盖它。生成模块仍必须根据提供片段判断不足，但其语义判断需要更多独立数据评测。

## 真实模型输出

生成配置为交大 `https://models.sjtu.edu.cn/api/v1` 的 `deepseek-chat`，使用真实本机凭据，密钥未写入仓库。对前两个检索样例执行 answer/draft/extract，间隔至少 7 秒，不做自动重试：

- `attention`：组合生成评测返回 `AI_INVALID_RESPONSE`；严格契约拒绝该次不合格输出。当前记录没有逐方法保存失败阶段，不能把它算成三方法全部成功，也不能据此推断具体失败原因。
- `positions`：三方法均实际返回并通过 Schema/来源范围校验；回答含两条合法来源，证据字段保持 `pending_verification` 或不足，草稿只生成内容。

生成原文和来源仅保存在执行环境的 `.runtime/sjtu-generation-review.json`，不提交论文大段原文。执行 agent 对成功样例做了非盲的局部检查：回答中的位置编码表达和引用页相符；草稿将原文关于相对位置能力的假设说得更肯定，仍需人工修订。该检查不是独立专家标注，不能作为事实准确率、蕴含正确率或全面防幻觉认证。保留失败和措辞问题，避免把结构校验等同于研究结论质量。

后续人工评测建议逐陈述核对：所引片段是否充分支持、是否引错页、是否扩大实验/范围/确定性、缺失字段是否正确标不足、合成来源是否仍明确标识；对无关输入和嵌入的恶意指令分别设计独立数据。应保留首轮输出与失败，不以不断重试后的成功样本代替实际成功率。

## 重现

先自行将上述版本的 PDF 下载到本机；评测脚本不会下载、访问业务库或自动寻找密钥。离线关键词测试无需配置任何 API：

```bash
.venv/bin/python scripts/evaluate_ai.py --pdf /path/to/1706.03762v7.pdf \
  --output .runtime/keyword-results.json
```

准备真实配置及本地模型，见 [C 接入说明](../ai-adapter.md)。明确启用真实模型后运行三模式，`--generation-cases 2` 最多对前两个查询各执行三次生成；未指定时只测检索：

```bash
.venv/bin/python scripts/evaluate_ai.py --pdf /path/to/1706.03762v7.pdf \
  --modes keyword semantic hybrid --live-models --generation-cases 2 \
  --review-output .runtime/generation-review.json --output .runtime/sjtu-results.json
```

`--request-interval` 默认 7 秒，可按服务额度调整；适配器本身不自动限流重试。评测失败不会打印供应商正文、密钥或论文内容。生成质量需要检查 review-output 中的实际结果，不应只看状态码。

## 契约与业务回归

无外部模型调用的验证结果：

- 全后端：**223 passed，37 skipped**。跳过项属于既有真实数据库门槛（6）和历史 SQLite 实验（31），未算作本次真实 PostgreSQL 联调通过。
- C 实现 provider 与独立用例：**56 passed**，包含原有 10 项共享方法契约。隔离 DataPort 和模型 HTTP 替身只验证行为，不能替代上方真实评测。
- OpenAPI 与 C/D Mock 示例检查、Ruff、依赖 `pip check` 通过；不修改前端，无新增公开端口或 Schema。

尚未实测真实 PostgreSQL 与 C 的组合、实际团队大库性能、中文学术语料质量或生产认证。A/B 对接和当前 B 全项目 top-50 后筛选论文的局限，见接入文档。
