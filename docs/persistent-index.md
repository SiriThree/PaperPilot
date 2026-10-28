# A/C 持久化索引

显式运行 `scripts/manage_postgres.py migrate` 安装新增的 `002_process_indexes.sql`；不修改 A 的 001 迁移，不在启动时迁移业务库。

持久化 A+C 启动必须设置 `PAPERPILOT_AI_EMBEDDING_REVISION`。模型身份包含 provider（独立嵌入服务 URL 或 fastembed）、模型名、权重版本、维度和 cosine 距离；不得把不同权重声明为同一版本。实际 512 维本地模型与测试 3 维替身各用自己的配置，A 的历史 `EMBEDDING_DIM=3` 预检常量不控制索引。

原有五方法 AIPort、26 方法 DataPort 和 HTTP schema 保持不变；B 在双方提供私有协作方法时使用 `process_indexed` / `complete_indexed_job`。解析得到的完整文本块（不超过 4000 字符，仍按页分块）写入 chunks.content；公开 excerpt 为同一文本块，不宣称保存完整页面或 OCR。模型输入为标题及文本块。C 先在事务外生成规范化向量，A 再在一个事务中提交块、模型、向量、process 结果、active pointer、ready 和 outbox。失败全部回滚；已有历史来源不可变，模型版本使用独立 chunk ID。

完成重放须同时匹配结果与索引指纹；旧 attempt、不同维度或同版本已有向量变化均拒绝。重启后语义检索只嵌入问题，文档向量从 PostgreSQL 读取。每次仍复查项目成员及来源权限。当前候选预算内使用精确 cosine 点积及既有 BM25/RRF；不是 ANN 大规模性能验收。

没有指定版本索引的历史论文（包括早期真实解析和 Mock 索引）在语义/组合检索时返回 `INDEX_NOT_READY`，必须明确重新处理，不能悄悄回退到 Mock 或临时嵌入。关键词检索可继续使用既有来源。没有开放批量重建的 HTTP 接口。

确认后台 worker 已配置真实 C 及目标模型版本、原 PDF 仍位于正确 storage root 后，可使用应用的受限业务连接环境执行单篇重建：

```powershell
python scripts/reindex_paper.py --actor <项目负责人UUID> --project <项目UUID> --paper <论文UUID> --request-id <本次操作固定UUID>
```

此工具只排队，不替代后台 worker，也不承诺读取到了 worker 的环境配置。实际模型身份以最终 process_indexes / embedding_models 为准。相同 request-id 幂等；同论文已有待处理/在执行 process 时拒绝新任务。仅负责人可操作，历史来源/索引不删除。重建失败可从原任务明确重试；未知提交重用原 request-id 核对，不另建任务掩盖状态。

验证：`scripts/verify_integration.py backend/tests/test_persistent_index.py`。覆盖原子提交/回滚、过期尝试、提交重放、版本与维度冲突、重启只嵌入问题；模型端点为本地替身，不代表外部模型质量。
