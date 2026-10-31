# C / D 联合 HTTPS 验收与演示

日期：2026-10-31（Asia/Shanghai）。已将成员 C 的论文模型能力接入原有 <https://localhost:8443>，使用原 `owner` 账号、数据库与部署编号。用户选择先导入 6 篇真实论文，并授权将它们的选定片段发送给现有 DeepSeek 服务完成生成验收。

## 远程 PR 与本地整合

| PR | 内容 | 固定 head |
| --- | --- | --- |
| [#11](https://github.com/SiriThree/PaperPilot/pull/11) | 真实文献、证据提取协议、NLP 字面量及范围回归 | `de770b833b75a36d3920cdbb5baa3f824fe12471` |
| [#15](https://github.com/SiriThree/PaperPilot/pull/15) | 300 篇语料目录、导入工具、Unicode 与长论文推理修复 | `687b438dbb797a572ec5694a88f19aa9b09c011c` |
| [#16](https://github.com/SiriThree/PaperPilot/pull/16) | 句段切块、实际 tokenizer 预算、历史来源兼容 | `af32993f947fcc91b8e78eba801ce166d4229469` |
| [#17](https://github.com/SiriThree/PaperPilot/pull/17) | SQL 精确余弦 Top-K、流式 BM25、全库范围过滤 | `15437271b39860975e613c3c4fee4e4cad7ed39a` |

读取时四个 PR 均未合并，后三个为草稿。按依赖链在本地整合 #17 的固定 head，未修改任何远程分支、PR 状态或评论。整合前已保存本地恢复提交 `689007a`。PR 中 300 篇语料的历史报告继续作为 C 的历史证据保存，不当作本次本机数据量。

Windows 兼容处理保留了原验收内容：发布语料 JSON 使用 LF，逐文件核对与 C 原提交字节一致，不修改已发布哈希；测试子进程统一 UTF-8。批量 300 篇工具的 POSIX 锁延迟到 CLI 入口加载，Windows 可以运行协议单测与六篇导入，完整批量工具仍要求 Linux。

## 当前运行组合

- 部署编号：`458fa5ff-8a69-49f2-be6e-104fc2a8476d`。
- 源码指纹：`8fc3d069ea12f553d4d9bd699247c259c78f7108c26d77757e6168a51df9ef02`。
- 镜像标签后缀：`8fc3d069ea12`；A=persistent、C=real、D=real，仍使用 schema 8，无新增数据库迁移。
- 统计与文献生成：现有 `deepseek-flash`，显式 JSON 对象协议，配置与密钥来自受保护文件，不进入 Git、镜像或 Compose 环境变量。
- 本地向量：`BAAI/bge-small-zh-v1.5`，512 维，固定快照 `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`。五个模型文件逐项 SHA-256 校验、只读挂载，运行时离线使用。句段 V2 正文目标 384 token，重叠 48 token，保留原文切片和物理页码。
- 业务身份仅为三个索引表增加 SELECT / INSERT；独立认证、审计、outbox、项目读取身份和 D 的 AST、参数绑定、执行预算及失败关闭均保留。上传卷归应用 UID 10001，后端只读根文件系统及端口边界保留。
- 新文献项目通过显式管理员操作供应各自读取身份，未改成共享宽权限统计连接。后续新项目仍需该操作及后端重启。

## 本机实际论文

| 项目 | 论文 | PDF 页数 | 当前真实片段 |
| --- | --- | ---: | ---: |
| 检索增强生成与可靠问答 | RAG（2005.11401v4） | 19 | 77 |
| 同上 | DPR（2004.04906v3） | 13 | 60 |
| 同上 | Lost in the Middle（2307.03172v3） | 18 | 76 |
| Transformer 与长文本理解 | Attention Is All You Need（1706.03762v7） | 15 | 45 |
| 同上 | BERT（1810.04805v2） | 16 | 69 |
| 同上 | Longformer（2004.05150v2） | 17 | 75 |

合计 **6 篇、98 页、402 个非 synthetic 片段、402 条 512 维向量、6 个成功提交的处理索引**。PDF 从 C 固定目录中的官方 arXiv 版本下载，逐个 SHA-256 匹配，通过正常 HTTPS 上传和后台处理入库。未直接插入预制片段、向量或答案。

状态文件：`.runtime/production/literature/corpus-state.json`。旧三个项目与旧卷保留；新增两个真实文献项目，不覆盖原有数据。

## 验证结果

| 检查 | 结果与记录 |
| --- | --- |
| 完整隔离 PostgreSQL 后端回归 | **1162 passed / 35 skipped / 0 failed / 0 errors**，`.runtime/production/c-d-final.xml` |
| 当前 HTTPS 安全链路 | **41 项通过**，`.runtime/production/evidence/acceptance.json`；包含模型查询、独立权限、CSRF、审计漂移、撤权、重启及空语料不捏造来源 |
| 当前真实文献工作流 | **20 项通过**，`.runtime/production/evidence/research-acceptance.json`；检索、选定论文问答、五字段证据、草稿、保存导出、跨项目拒绝和重启保留 |
| 浏览器 | 文献研究与安全页面各 **1 项通过**；真实组合检索、原文展开、已存证据和报告、文献项目内 D 查询；安全用例在 `c-browser.log` 的组合运行中通过，研究用例修正导航 ARIA 选择器后在 `c-research-browser.log` 独立复跑通过 |
| 构建与静态检查 | Ruff、改动 Python 格式、OpenAPI、前端类型／构建、pip check、diff 检查通过 |
| 隔离恢复 | `.runtime/production/backups/20261031T092712Z-ddfd0478`；表计数／迁移、模型文件、六个上传 PDF 哈希、认证／权限／原生查询以及恢复库的本地关键词和语义检索通过 |

全量回归已显式启用 C 的 PostgreSQL 检索和重切块测试。35 个跳过为 31 个历史 SQLite、2 个 POSIX 文件权限、1 个额外真实模型启动 opt-in、1 个预构建干净镜像 opt-in；不计为通过。回归中的供应商响应替身与上述真实 DeepSeek 调用分开记录。

备份只向随机名称、带所有权标签且无外网的临时数据库恢复，未向正式库覆盖恢复。备份含密码、角色验证信息和 CA 私钥，保留在受保护目录，不上传 Git。正式服务在演练后恢复运行。

## 按页面演示

按 Ctrl+F5 刷新 <https://localhost:8443>，使用原 owner 账号登录，进入“检索增强生成与可靠问答”。

| 功能 | 操作 | 观察结果 |
| --- | --- | --- |
| PDF 解析与入库 | 文献工作台查看三篇论文，再进入研究工作台选择 RAG | 可检索，片段与真实页码可读 |
| 三种检索 | “检索结果”中输入 `retrieval augmented generation`，分别选关键词、语义、组合，点击检索 | 实际结果与页码；点击“展开来源”定位原文片段 |
| 文献问答 | 右侧选择生成材料，仅勾 RAG，问“RAG-Sequence 和 RAG-Token 在生成时如何使用检索文档？” | 实时任务与带引用回答，点击引用核对原文 |
| 证据提取 | 左侧选择 RAG，右侧“证据”→“查看已有证据”或“提取证据” | 五字段；支持、未提及、证据不足状态按实际结果显示 |
| 报告 | 右侧“报告”中打开“RAG 文献模型验收（待人工核验）”，或生成新草稿 | 正文、引用、版本和文本导出；编辑后保存新版本 |
| D 查询 | 同项目进入“查询与审计”，问“每年分别有多少篇论文？” | 模型候选经 D 审核，真实执行，当前项目合计三篇 |
| 操作审计 | 刷新审计、按事件筛选并查看详情 | 查询 SQL、问题、文档与任务操作，通过请求编号关联 |
| 当前部署状态 | 点击“重新检查当前服务” | 显示统计／文献模型、本地向量与匹配当前版本的安全验收 |

“待人工核验”保留，表示需要人工对照引用检查生成内容的语义。实时模型失败会保留实际任务和错误，不替换成历史成功结果。

## 复验与维护

```powershell
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py status
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_deployment.py verify
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/verify_production_research.py
.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/production_backup.py
```

文献复验复用本版本已记录的原任务 ID；失败或状态不明时应查询原任务，未经确认不重新导入或新建同一生成任务。六篇导入工具也保留稳定上传幂等键，并支持只在内存中传入已有凭据；没有另存纯密码副本。

后续项目的 D 读取身份需要操作者执行 `setup bind-project-reader --project-id <项目UUID>`，然后重启后端加载；该命令仅接受当前部署 owner 已有项目，身份仍严格绑定单个项目。Compose 调用方式见生产部署说明。

本次没有导入完整 300 篇，没有复跑 C 的 300 篇吞吐／召回率实验；没有宣称模型语义质量、全部业务底表原生 RLS 或公网生产部署已验收。
