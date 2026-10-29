# 成员 C：真实文献与 PostgreSQL 演示

本次演示基于主分支 `2586767`，开发分支 `feat/member-c-real-literature`。使用真实公开论文、真实本地 PostgreSQL 和真实模型调用。文献通过现有登录、上传、后台处理接口入库，没有用手写段落代替论文内容，也没有直接插入预制答案。

## 当前机器已经准备的环境

| 项目 | 配置 |
| --- | --- |
| PostgreSQL | 16.15，用户目录安装，监听 `127.0.0.1:54327` |
| pgvector | 0.8.7 |
| 数据库 | `paperpilot_research` |
| 应用账号 | `pp_business_research`，无超级用户、BYPASSRLS、建库或建角色权限 |
| A / C / D | `persistent` / `real` / `real` |
| 登录 | credentials，Alice 的密码保存在本机权限为 600 的文件中 |
| 生成 | 已有 SJTU 服务，`deepseek-chat`，显式 `json_object` 模式 |
| 嵌入 | 本地 `BAAI/bge-small-zh-v1.5`，512 维 |
| 嵌入版本 | `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59` |
| 页面 / API | `http://127.0.0.1:15174` / `http://127.0.0.1:18801` |

安装目录为 `~/.local/opt/postgresql-16.15`，集群在仓库的 `.runtime/real-demo/postgres`。该方式不需要 sudo，不修改系统服务。PostgreSQL 源码取自[官方 16.15 发布目录](https://www.postgresql.org/ftp/source/v16.15/)，与同目录的 SHA256 文件核验后编译；pgvector 使用[官方仓库](https://github.com/pgvector/pgvector)的 `v0.8.7` 标签。

这是共享业务身份的本地开发组合。共享认证需要 `auth_sessions`、`auth_login_limits` 的读写权限及 `users` 的行锁列权限；最新审计路径需要 `paperpilot_audit` schema、指纹函数和项目视图的访问权限。权限已由本机初始化脚本显式配置。没有开启 SQL 执行候选路径、数据库 RLS 试点或生产审计预检，不能把这次文献演示当成这些能力的验收。

## 真实文献

目录和问题见 [`demo/real-literature.json`](../demo/real-literature.json)。固定 arXiv 版本、文件 SHA256、作者及物理页数；PDF 本体留在 `.runtime`。

| 项目 | 论文与固定版本 | PDF 页数 | 原文片段数 |
| --- | --- | ---: | ---: |
| 检索增强生成与可靠问答 | [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401v4) | 19 | 70 |
| 同上 | [Dense Passage Retrieval for Open-Domain Question Answering](https://arxiv.org/abs/2004.04906v3) | 13 | 58 |
| 同上 | [Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172v3) | 18 | 67 |
| Transformer 与长文本理解 | [Attention Is All You Need](https://arxiv.org/abs/1706.03762v7) | 15 | 40 |
| 同上 | [BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding](https://arxiv.org/abs/1810.04805v2) | 16 | 68 |
| 同上 | [Longformer: The Long-Document Transformer](https://arxiv.org/abs/2004.05150v2) | 17 | 75 |

共 6 篇、98 页、378 个原文片段，378 条 512 维向量和 6 个处理索引提交记录。两个项目分别登记模型版本。这些数量来自数据库查询，不是清单预估。

每个项目准备了 6 个问题，包括一个材料外问题。预期页码指 PDF 从 1 开始的物理页码。页码重合只能帮助人工定位，不能证明回答的每项陈述都被原文支持；这组问题属于开发演示材料，不是独立测试集或模型效果基准。问题清单可供 B 接入推荐问题按钮，目前不声称页面已经接入这些按钮。

## 当前机器的启动和停止命令

以下路径均从 `/home/huyi/Codes/PaperPilot` 执行。本机依赖、数据库初始化、模型权重、秘密文件和配置清单均已准备，启动不需要重新安装。

```bash
cd /home/huyi/Codes/PaperPilot

# 数据库已运行时不用执行 start；先看状态。
$HOME/.local/opt/postgresql-16.15/bin/pg_ctl \
  -D "$PWD/.runtime/real-demo/postgres" status

# 仅在数据库未运行时启动。
$HOME/.local/opt/postgresql-16.15/bin/pg_ctl \
  -D "$PWD/.runtime/real-demo/postgres" \
  -l "$PWD/.runtime/real-demo/postgres.log" start

# 前台启动后端、outbox 投递器及前端。已有该演示运行时无需重复执行。
.venv/bin/python scripts/start_research_demo.py \
  --provider-config "$HOME/.openclaw/openclaw.json"
```

登录账号 `alice`，密码见 `.runtime/real-demo/secrets/alice-password.txt`。启动器在内存中读取已有 SJTU 配置，不将密钥放在命令行或提交到 Git。停止应用可在启动终端按 Ctrl+C，或在另一个终端执行：

```bash
cd /home/huyi/Codes/PaperPilot
touch .runtime/real-demo/app.stop

# 不再使用时，可单独停止此数据库；不删除数据。
$HOME/.local/opt/postgresql-16.15/bin/pg_ctl \
  -D "$PWD/.runtime/real-demo/postgres" -m fast stop
```

原有 `15173` 的临时预览与本演示分开，本命令不停止它。

## 可重复导入

```bash
cd /home/huyi/Codes/PaperPilot
.venv/bin/python scripts/seed_real_literature.py \
  --api-base http://127.0.0.1:18801 \
  --origin http://127.0.0.1:15174 \
  --password-file .runtime/real-demo/secrets/alice-password.txt \
  --download
```

`--download` 仅下载缺失的 PDF；已有文件必须通过版本哈希校验，错误文件不会被覆盖。稳定项目标记和上传幂等键使重复执行复用原项目、论文和处理任务。失败任务默认保留；确认原因后可显式加 `--retry-failed` 重试原任务一次。任务未确定时应查询原任务，避免换键重复导入。

运行状态保存在 `.runtime/real-demo/corpus-state.json`，包含项目、论文及原处理任务 ID。相同清单导入另一套服务时，用 `--state` 指定另一份状态文件。上传后的检验包括可检索状态、原文片段非空、`synthetic=false` 和文献身份匹配。

`scripts/start_research_demo.py` 用于启动已由操作者准备的环境，不负责安装或迁移。它需要 `.runtime/real-demo/database.json`（数据库地址、业务账号、数据库名、密码文件路径、登录用户名、登录密码文件路径）、`scripts/prepare_ai_embeddings.py` 生成的模型清单，以及 `--provider-config` 指定的配置。Provider 配置支持现有 OpenClaw registry，或单独的 `{ "baseUrl": "…", "apiKey": "…" }` 文件。其他机器应通过现有 `manage_postgres.py`、`manage_security.py` 等显式准备自己的数据库和账号，不复制本机秘密文件。

## 答辩演示顺序

1. 登录后打开“检索增强生成与可靠问答”，查看三篇文献均可检索。
2. 打开 RAG 原文，提出“RAG-Sequence 和 RAG-Token 在生成时如何使用检索文档？”。
3. 点击回答引用，核对文献标题、页码与原文片段；说明结构校验和原文身份校验的作用，以及仍需人工核验语义。
4. 切到 RAG 论文的证据页，点击“查看已有证据”，读取已保存的五字段。当前研究局限返回未提及，表示这次所供片段未明确支持该字段；不是格式失败。
5. 提出材料没有涵盖的问题，观察明确的证据不足状态。
6. 打开已保存的 AI 研究笔记草稿；说明报告、引用快照及版本均持久化，而内容仍待人工核验。

证据提取有上下文预算：服务最多选单篇前 50 个片段、60,000 字符，适配器还会执行自身预算。因此不能声称所有论文都完整送入模型，也不能把未提及字段理解成整篇论文绝对没有相关内容。实时调用可能超时，应展示实际任务状态；超时不能用预制成功答案替代。

本次真实调用和重启检查记录见 [`real-literature-acceptance.md`](real-literature-acceptance.md)。含原文片段的完整任务结果留在忽略目录，不随 PR 分发。
