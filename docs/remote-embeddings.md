# 使用 4router 替换本地 BGE 嵌入

嵌入服务使用 `https://4router.net/v1` / `text-embedding-3-large`，默认 3072 维。
地址来自 [4router 官方配置说明](https://docs.4router.net/guides/manual-config/)，已实际请求模型列表和嵌入接口确认可用。
生成回答仍使用原 SJTU `deepseek-chat` 配置，两种服务各自使用独立凭据。

嵌入模型负责将论文片段与检索问题转换为向量。切换之后，语义搜索、混合搜索和 RAG 的证据检索使用新向量；关键词搜索继续使用原倒排索引。当前没有人工相关性标注，不能据模型名称声称检索准确率提升。

## 切块和索引

保留原 `sentence-token-v2` 句段切块及 BGE tokenizer 的 384/48 预算，保持 300 篇文献的页码、摘录和边界可对照。该 tokenizer 仅用于切块，不运行 BGE 推理；新增 `PAPERPILOT_AI_CHUNK_TOKENIZER_DIR` 使它与生成向量的模型目录分离。

远端 `text-embedding-3-*` 输入再使用 `tiktoken` 的 `cl100k_base` 计数，包含论文标题；超过 8192 token 或空白输入在任何批次发送前拒绝，不截断原文。按 32 条输入分批，显式请求配置维度，检查返回索引、维度、有限数值和非零范数并归一化。[模型与维度说明](https://developers.openai.com/api/docs/guides/embeddings)、[嵌入 API 输入限制](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create)。

模型标识包含 provider URL、模型名、revision 和维度。`revision` 是运维指定的索引配置版本；4router 未提供可验证的权重提交号，不能把此字段当作上游权重哈希。旧 BGE 512 维索引不能与新查询向量混用。

本机迁移使用独立的 `paperpilot_4router` 数据库及上传目录，保留 BGE 的 `paperpilot_chunking_v2`、基线数据库和公开测速记录。导入同一份真实 300 篇 PDF，通过正常上传/任务接口生成新向量；切换模型本身不构成与 BGE 的公平速度比较。远端延迟包含网络和服务端处理，3072 维也会增加向量存储及精确距离计算成本。

## 真实语料验收（2026-11-01）

已完成同一批 300 篇真实 PDF 的重建：300 个成功处理索引、20,986 个来源和同量 3072 维向量，合成来源为 0，数据库约 381.05 MiB。逐条比对缓存原文后，页码与摘录完全一致；新模型完整输入最大 1926 个 cl100k_base token，低于 8192 上限。保留的切块 tokenizer 统计最大为 422/512，二者计数规则不同。

关键词、语义、混合检索在全库均返回 10 个真实来源；浏览器实际验证 HTTPS 登录、三种检索、来源展开/跳转及原文摘录一致，Secure/HttpOnly 会话有效，没有页面脚本错误。复跑导入没有新增论文、索引、向量、任务或尝试。BGE 原库的 300 篇 / 20,986 个来源及向量内容摘要保持不变。

导入期间保留了 20 次失败尝试（16 次任务超时、4 次 AI_UNAVAILABLE），通过显式任务重试恢复，最终 300 个任务全部成功；不隐藏失败或标记为首次全成功。模型客户端不自动重试，最后的导入阶段使用两个 API 进程、最多两个待处理任务，100 片段及以上的长论文单独处理。验收页面恢复为一个 API 进程。真实服务曾出现连接/超时波动，不能用少量冒烟请求推断吞吐能力或稳定性。

常规回归 726 passed / 480 skipped，另有 13 项显式隔离 PostgreSQL 测试通过。没有测量人工相关性或建立新模型的查询速度基线。

公开记录：[数据快照](../demo/4router/summary.json)、[逐条来源与 token 核验](../demo/4router/ingestion-check.json)、[BGE 保留检查](../demo/4router/baseline-check.json)、[检索冒烟检查](../demo/4router/search-smoke.json)、[浏览器检查](../demo/4router/browser-check.json)、[导入幂等](../demo/4router/idempotency-check.json)、[验证汇总](../demo/4router/checks.json)。token 总数为最终索引文本的本地计数，不含重试和诊断请求，不是供应商账单 usage。

## 本地配置

安装项目 `ai` extra（本次依赖快照为 `requirements-ai-remote.lock`）；句段 V2 另需 `pysbd` 和 `tokenizers`，可复用已有 `ai-local` 环境。启动前准备公开 tokenizer 资产：

```bash
python -m pip install -e '.[ai,ai-local,data]'
python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
```

密钥保存到当前用户拥有的绝对路径普通文件，目录 0700、文件 0600。配置文件只引用该文件，不把密钥写入源码、命令参数或 PR。示例 `.runtime/4router/embedding.json`：

```json
{
  "base_url": "https://4router.net/v1",
  "api_key_file": "/absolute/private/4router-embedding.key",
  "model": "text-embedding-3-large",
  "dimensions": 3072,
  "revision": "4router-text-embedding-3-large-20261101-d3072-v1",
  "chunk_tokenizer_dir": "/absolute/prepared/bge-tokenizer-directory"
}
```

准备独立数据库、账号、上传目录和本地 HTTPS 证书后运行：

```bash
PYTHONPATH=backend python scripts/start_research_demo.py \
  --state-dir .runtime/4router \
  --embedding-config .runtime/4router/embedding.json \
  --provider-config /absolute/private/sjtu-provider.json \
  --api-port 18807 --web-port 15180
```

`--embedding-config` 覆盖本地 BGE 嵌入配置；存在独立 chunk tokenizer 时启动器的 `auto` 保持 V2。其余环境启动可设置 `PAPERPILOT_AI_EMBEDDING_BACKEND=remote`、`...EMBEDDING_BASE_URL`、`...EMBEDDING_API_KEY_FILE`、`...EMBEDDING_MODEL`、`...EMBEDDING_DIMENSIONS`、`...EMBEDDING_REVISION`，并显式设置 `PAPERPILOT_AI_CHUNK_STRATEGY=sentence-token-v2` 及 `...CHUNK_TOKENIZER_DIR`。

如果本机网络需要代理，可在该 JSON 中加 `"proxy_url": "http://127.0.0.1:7890"`，或设置 `PAPERPILOT_AI_EMBEDDING_PROXY_URL`。只接受显式指定、无 URL 凭据的 loopback HTTP CONNECT 代理，HTTPS 目标仍验证证书。代理仅用于嵌入请求，SJTU 聊天连接不受影响，也不读取全局代理环境变量。本次本机直连曾出现连接超时，经已有本地代理的实际嵌入探测成功。

本地是 development / shared-auth 演示，不代表生产环境的角色隔离已验收。本次最终导入使用 `--api-workers 2`，配合导入脚本的 `--max-pending 2 --serialize-above-chunks 100`，完成后恢复一个进程。导入脚本支持 `--tls-ca` 信任已准备的本地证书；不关闭 HTTPS 校验。
