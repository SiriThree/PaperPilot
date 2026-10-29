# 真实文献演示验收记录（2026-10-29）

基线主分支 `2586767`，分支 `feat/member-c-real-literature`。环境与操作命令见 [real-literature-demo.md](real-literature-demo.md)，不含原文的机器记录见 [JSON](ai-evaluation/real-literature-20261029.json)。完整任务正文与原文片段仅保存在本机 `.runtime/real-demo/`。

## 已验证的真实环境

- PostgreSQL 16.15 + pgvector 0.8.7；2 个项目、6 篇真实论文、98 页、378 个原文片段和 378 条 512 维向量。
- 下载内容与固定版本 SHA256 一致；通过现有认证上传和后台任务建立索引。
- 第二次导入沿用原项目、论文和处理任务：仍为 2 / 6 / 378 / 378 / 6 个项目、文献、片段、向量和索引提交记录。
- 同时重启本次应用和 PostgreSQL 后，以上数量不变，2 份报告和首次 16 项任务的完整结果不变。
- 浏览器真实登录、读取论文、打开持久化报告均通过，页面异常为 0。另从网页提交 DPR 问题，真实模型回答成功，点击引用后定位到 DPR 第 2 页的真实片段，`mock=false`，页面异常为 0。

## 首次调用：保留失败

首次共 16 个任务，13 个状态为 succeeded，3 个 failed。成功状态中还有 1 个位置编码问题误报证据不足，因此不能将任务成功率当作回答正确率。两个材料外问题均明确返回证据不足。

| 问题或任务 | 首次实际结果 | 引用物理页或错误 |
| --- | --- | --- |
| `rag-evidence-rag-variants` | succeeded；有来源回答 | rag: 3,4,6,8,9 |
| `rag-evidence-dpr-encoding` | succeeded；有来源回答 | dpr: 1,2,3,5 |
| `rag-evidence-dpr-negatives` | failed | TASK_TIMEOUT |
| `rag-evidence-context-position` | succeeded；有来源回答 | lost-middle: 1,2,3,5,7,8,10,14,15 |
| `rag-evidence-retrieval-context` | succeeded；有来源回答 | rag: 6,8；lost-middle: 5,7,12 |
| `rag-evidence-not-in-corpus` | succeeded；证据不足 | 无引用 |
| `rag-evidence-evidence` | failed | AI_INVALID_RESPONSE |
| `rag-evidence-draft` | succeeded | 已保存 AI 草稿和引用快照，待核验 |
| `transformer-context-scaled-attention` | succeeded；有来源回答 | attention: 4 |
| `transformer-context-positions` | succeeded；证据不足 | 无引用 |
| `transformer-context-bert-objectives` | failed | AI_INVALID_RESPONSE |
| `transformer-context-longformer-pattern` | succeeded；有来源回答 | longformer: 1,3,4 |
| `transformer-context-different-benchmarks` | succeeded；有来源回答 | attention: 1,6,8,9,10；bert: 6,8,9,14 |
| `transformer-context-not-in-corpus` | succeeded；证据不足 | 无引用 |
| `transformer-context-evidence` | succeeded | 五字段待核验，引用均与数据库原文一致 |
| `transformer-context-draft` | succeeded | 已保存 AI 草稿和引用快照，待核验 |

首次所有已返回来源都通过了原文身份检查：paper_id、chunk_id、页码、excerpt 和 synthetic 标记与授权数据库记录一致。该检查验证来源未被替换；不自动验证陈述是否被原文充分支持。

## 修复与明确复测

1. 位置编码问题：原流程从全项目截取 50 个片段后再筛选选中文献，真实诊断中仅留下 Transformer 第 2、3、8、9、10 页，漏掉第 6 页。改为先限制选中文献再排序后，40 个片段中包括第 6 页；完整任务回归覆盖了目标文献排在全项目第 50 名以外的情况。
2. BERT 正文校验：旧规则会拒绝所有 ASCII 方括号，连 `[MASK]` 等技术术语也无法表示。现在仅放行五种明确的 NLP token 字面量；`[99]`、`[source]` 和嵌套方括号仍被拒绝，引用仍由应用根据 citation_ids 渲染。首次 BERT 调用未保存供应商原始响应，所以不把这项代码缺陷断言为那次失败的唯一原因。
3. 超时不换任务；对 3 个首次失败任务各明确重试一次，保存首次与复测记录。位置编码首次任务已成功结束，因此复测使用新任务，原记录仍保留。

| 复测项目 | 方式 | 结果 |
| --- | --- | --- |
| `transformer-context-positions` | new_after_scope_fix | succeeded；引用页 6 |
| `transformer-context-bert-objectives` | retry_original_failed_task | succeeded；引用页 2,4,12 |
| `rag-evidence-dpr-negatives` | retry_original_failed_task | succeeded；引用页 3,6 |
| `rag-evidence-evidence` | retry_original_failed_task | failed：AI_INVALID_RESPONSE |

上表保留修复前的历史状态：当时 RAG 首次字段状态不一致，重试时响应未通过严格解析，失败结果未写入有效证据。Transformer 的五字段提取已成功保存。随后完成的 RAG 提取格式修复及验证见下文，历史失败记录不覆盖。

## 回归与展示材料

- 默认后端回归：590 passed / 428 skipped / 0 failed；跳过项仍未宣称全部验收。
- 使用独立测试数据库的 PostgreSQL DataPort 契约：41 passed。
- 真实模型调用为上述单次开发记录，不能替代独立语义评估。
- 本机截图：`.runtime/real-demo/projects.png`、`real-paper-reader.png`、`persisted-report.png`、`real-answer-citation.png`。图为实际页面截图，未生成或替换页面中的答案。

## 五分钟内的展示建议

先用 DPR 问答展示“选文献—真实生成—引用定位”，随后选 RAG 论文读取已保存的五字段证据，最后打开已保存报告，展示 PostgreSQL 重启后版本和引用仍在。材料外问题可用于展示证据不足。

## RAG 提取格式修复

后续修复以 `2bd7d7c` 为基线，机器记录见 [rag-extraction-format-20261029.json](ai-evaluation/rag-extraction-format-20261029.json)。本轮对旧流程的两次真实诊断均成功，说明历史问题具有间歇性；历史失败没有保留供应商原始响应，不能把某个字段或截断原因断言为其唯一成因。

代码中可确认的协议问题是：同一提示同时要求问答的 `insufficient_evidence` 布尔值/`statements` 数组，以及提取的五字段格式。现在分开这两种提示，提取协议明确要求每个字段的状态、正文和引用关系；模型 Schema 的字段说明同步明确约束，私有模型解析增加状态一致性校验。提取使用简短中文，控制返回篇幅。未放宽 JSON、字段类型、截断、引用或来源校验，也不自动补字段、删除异常正文或隐式重试。

修复后验证：

- 一次直接使用同一批 RAG 原文的真实模型调用成功。
- 再从真实网页点击“提取证据”，任务 `30e6fd02-8d4c-4e1c-a60b-40bd1c052b3a` 首次尝试成功，`mock=false`。
- 研究问题、方法、结果、结论为有引用的待核验文本；研究局限为 `not_mentioned`、null 正文、空来源，状态一致。缺失状态仅描述本次供给片段，不断言整篇论文绝对没有相关内容。
- 所有引用的文献、片段、页码、正文与数据库原文一致；网页点击支撑来源可正确定位。
- 结果已写入 PostgreSQL，刷新网页后“查看已有证据”能重新读取；浏览器异常为 0。
- AI 契约 91 passed；默认后端 618 passed / 428 skipped / 0 failed；OpenAPI 和模块样例校验通过。

实际页面截图在本机 `.runtime/real-demo/rag-format-fix/rag-evidence.png`。以上是实际模型与页面的开发验证，不能保证后续调用没有服务波动或每项陈述都被充分支持，内容仍待人工核验。
