# PaperPilot 工作台交互研究与设计提案

研究日期：2026-10-29。状态：供评审，尚未获准实施。范围：前端审查、开源交互源码研究和方案设计。

## 1. 结论与推荐

推荐采用**方案 A：紧凑文献库 + 阅读/写作双区**。保留项目边界与现有 API，把上传改成按需面板，把来源定位融入阅读区，把问答、证据、报告放入可切换的辅助区。先修复编辑保护和范围表达，再调整布局。不要以接入 PDF 阅读器作为首轮前端重构的前置条件。

最值得优先解决的不是配色，而是：

1. 同一个研究问题下，检索的年份/模式与生成任务的文献范围不同，界面没有清晰解释。
2. 报告有路由离开保护，但页内打开其他报告或版本会直接覆盖当前编辑。
3. 来源阅读、来源抽屉、问答引用之间存在重复展示与遮挡。
4. 上传、标题、说明、流程条长期占据空间；主工作内容处于多层卡片内。
5. 选中文献、查询、结果和视图状态主要留在页面内，跨页面往返会重建。

当前没有公开原 PDF 获取接口，也没有来源坐标。**本轮可交付片段阅读和片段级引用定位；完整 PDF、页内精准框选高亮应列为有依赖的后续能力。**不能把页码和片段文本包装成已经实现的 PDF 定位。

## 2. 研究方法、版本与验证边界

### 2.1 证据等级

- **运行验证**：在本地独立 Mock 服务与独立上传目录上运行现有 Playwright 测试，浏览器为无头模式；查看了本次生成的 1440 × 1000 研究页截图。
- **源码确认**：阅读组件模板、事件处理、状态及 API 调用；可解释交互如何发生，但不等同于亲自操作部署产品。
- **设计判断**：基于以上证据提出的取舍、尺寸、工期和验收目标，均非已测得的竞品指标。
- **未确认**：没有覆盖的行为明确保留，不以项目功能宣传代替验证。

六个竞品均成功访问 GitHub 仓库、递归文件树及实际核心源码。没有部署或登录这六个产品，没有执行其完整端到端流程，也没有实测它们的网络耗时、键盘覆盖率或移动端质量。本轮采用定点源码研究，没有安装它们的后端、数据库、账号或模型环境；不能据此声称这些项目无法运行。Zotero 最初尝试的 `item/items.jsx` 路径返回 404，随后通过实际文件树改读 `current-items.jsx`、通用表格和 library/reader 实现。

### 2.2 竞品固定快照

采用研究时各仓库默认分支的提交，分支不代表稳定发行版。以下链接固定到提交，避免后续默认分支变动改变证据。

| 项目 | 分支 | 提交 |
|---|---|---|
| Zotero Web Library | master | `4cfa48994ab31c67c9fe10311c4abe57c0882188` |
| AFFiNE | canary | `8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34` |
| RAGFlow | main | `97b08d6585ee51757ea3fdd28e0a6a1473b2abfc` |
| Paperless-ngx | dev | `c21bdab5c88ab0624fcde787049436598ece9e85` |
| Open WebUI | main | `33c1978d5bad52a31df75ea55143025dfad18d16` |
| Khoj | master | `ae229ca894c0b80ad84664afcfdde523b5e87057` |

### 2.3 本地验证

本次运行 `connected-workspace.spec.ts` 中两项既有测试，独立 API 端口 18129、前端端口 15129，结果 **2 项通过**：

- `paper selection, answer citation and report snapshots share the same source`：选择文献、来源加入报告并去重、保存引用快照、回答引用切换到相应片段。
- `project switch protects unsaved edits, then clears old project data after explicit discard`：跨项目切换提示未保存编辑，取消保留，明确放弃后清理旧项目数据。

结果记录：`output/ux-research-20261029/.last-run.json`。本次截图：`output/connected-workspace/desktop-linked-reading.png`。截图由既有测试主动滚动到研究流程区之后生成，**不能作为页面首屏高度测量依据**。它确认了侧栏、文献列、片段列、助手列的实际相邻关系，以及来源、问答和空检索结果的卡片嵌套。

没有运行全部现有测试，没有新增测试代码，没有验收真实 AI、真实 PDF 或正式数据库。研究期间工作区已有其他人的后端修改，且状态发生变化；没有回退、暂存或改动这些文件。

**并行变更说明**：初次审查时前端没有未提交改动；报告完成检查时，工作区另行出现 App、WorkspaceSidebar、main、ResearchPage 与前端测试的修改，以及新增 `affine-workspace.css`。这些不是本研究执行的编辑。只读 diff 显示其涉及侧栏折叠、助手标题和新样式引入。因此，下文的 232px 侧栏、四份全局 CSS 及卡片空间问题描述对应本轮初始读取/截图时的版本，不能视为对这些后来样式改动的验收；实施前需重新核对最终合并布局。U01/U02/U07 的核心函数在所见 diff 中未被改变。

## 3. 六个竞品的交互比较

以下为源码层面的观察。表中“未确认”只描述本次覆盖范围，不代表产品不支持。

### 3.1 信息架构、导航、列表与多任务

| 项目 | 信息架构/布局 | 导航与侧栏 | 搜索、筛选、排序、批量 | 多任务/多页面 | 对 PaperPilot 的启发与限制 |
|---|---|---|---|---|---|
| Zotero | library 容器协调馆藏、条目列表、详情；reader 为单独视图 | 桌面侧区与触屏导航分别处理；小屏按视图逐层进入 | CurrentItems 汇集查询、标签、选择；Table 接收排序、选中索引和列宽变化 | reader 保留阅读位置相关设置；不据此推断为任意分屏任务系统 | 用稳定选中行联动详情，避免每行一个大卡片；无需引入完整馆藏树 |
| AFFiNE | Workbench 管理 view；SplitView 组织并排面板 | 全局侧栏含快速搜索入口，工作区视图单独建模 | PageList 导出展示菜单、分组与虚拟列表；本次未完整追踪批量编辑提交 | 实体维护多视图与比例；支持拖放形成分屏 | 借鉴“视图状态与内容实体分开”；首轮不实现自由拖放多分屏 |
| RAGFlow | 数据集管理与聊天/来源查看通过文档 ID 相连 | 数据集侧栏与内容过滤栏分工 | ListFilterBar + DatasetTable；选择后出现 BulkOperateBar；翻页清理选择 | 文档预览 Sheet 按需创建；不等同完整文献阅读会话 | 批量工具栏只在选择后出现；知识库设置不应占据研究主流程 |
| Paperless-ngx | 文档列表与详情分离，详情有文档阅读区 | 保存视图和路由查询参数共同决定列表状态 | 列表支持筛选编辑器、排序、显示列、保存视图、批量选择 | 从保存视图/查询参数恢复列表；详情的其他状态未全面追踪 | 借鉴可恢复的列表；首轮只提供已有字段，不虚构标签/作者 |
| Open WebUI | 对话内容与来源详情分层 | 侧栏 SearchInput 提供标签、文件夹、固定/共享/归档对话的搜索建议 | 本次查看的是会话搜索，不是学术文献管理；论文表格批量未确认 | 侧栏围绕会话检索；完整跨页恢复逻辑未验证 | 来源默认收起，可展开后查看详情；PaperPilot 不应因此伪装已有持久化多轮会话 |
| Khoj | 问答输入、聊天侧栏、ReferencePanel 分工 | 输入可带工具/研究模式；聊天侧栏承载会话配置等上下文 | 参考来源按类型组织；论文筛选排序和批量本次未确认 | 参考面板按响应组织，Sheet 显示完整来源；多任务恢复未验证 | 把“本次回答的来源”与“项目文献库”分开，不堆满完整来源卡片 |

证据：[Zotero library][Z1]、[Zotero Table][Z2]、[AFFiNE Workbench][A1]、[AFFiNE 列表][A3]、[RAGFlow 数据集][R1]、[Paperless 列表模板][P1]、[Paperless 列表状态][P2]、[Open WebUI 搜索][O3]、[Khoj 来源面板][K1]。

### 3.2 阅读、证据、反馈、可访问性与性能

| 项目 | PDF 与 AI 上下文衔接 | 引用/原文定位 | 上传、加载、空/错状态 | 窄屏、键盘与可访问性 | 性能及维护性观察 |
|---|---|---|---|---|---|
| Zotero | reader 实际管理附件、注释、阅读状态；本次未发现可作为 PaperPilot 问答模板的 AI 链路 | 阅读位置、注释与附件关联，不宜把条目引用等同 AI 证据 | reader reducer 区分附件/状态加载及错误；全局 Ongoing/Messages | library 明确区分触屏/单列视图；Table 有多选和行数 ARIA；存在焦点恢复辅助 | 虚拟列表、初始滚动恢复和越界保护；复杂 Redux 状态不宜照搬 |
| AFFiNE | 附件 PDF viewer 文件存在；此次重点阅读分屏，未验证 AI 引用到 PDF 的完整链路 | 文档间跳转与 view 模型明确；PDF 坐标定位未确认 | 侧栏、列表、分屏独立；上传/空错全链路未验证 | 拖动改变比例明确；ResizeHandle 本次未见充分键盘调整证据，不能据此宣称可访问性完备 | react-virtuoso 虚拟列表、独立视图实体；自由分屏及编辑器体系维护成本高 |
| RAGFlow | PdfSheet 接收 documentId 和 chunk，生成高亮并传入 DocumentPreview | 使用 chunk.positions、highlights；PdfPreview 接入 PDF highlighter | 上传 hook 区分上传、可自动解析文件和部分成功；PDF 有 errorMessage | 数据集页存在 `min-w-[880px]`，是窄屏风险线索；完整实际表现未运行 | 条件挂载预览、独立请求 hooks；React 高亮库不能直接作为 Vue 组件使用 |
| Paperless-ngx | 独立 PDF viewer 管理页码、搜索和文档；所读代码没有 AI 问答衔接 | 阅读页码变更通过事件上报，支持搜索定位；不是 AI 溯源实现 | 列表有 reload 状态；viewer 发出 loadError 并销毁加载任务 | 工具栏使用 ARIA 标签和小屏隐藏样式；未完成键盘/读屏实测 | viewer 释放任务与 ResizeObserver；列表用服务、RxJS 与路由组织，不能机械翻译为 Vue |
| Open WebUI | 引用组件根据 source 类型打开详情/嵌入内容；PDF 文本定位能力未在本次确认 | 来源计数展开、来源按钮、详情模态框；详情合并文档及元数据 | 来源解码失败有回退，favicon 有错误回退；上传/任务错误未全面读完 | 来源开关有 aria-expanded，按钮有名称；不能据此声称整站符合 WCAG | 折叠来源减少正文占用；多来源归并与响应式状态应独立封装；外站 favicon 请求不应照搬 |
| Khoj | 参考面板展示笔记/网络/代码来源，不证明 PDF 坐标定位 | teaser → 完整 Sheet；可复制 Markdown 来源 | 输入组件区分 uploading/error；无来源时面板返回 null | 移动/桌面 teaser 数不同（3/5）；Sheet 可滚动；完整键盘行为未实测 | 来源派生和格式化独立；大型输入组件也体现单文件复杂度风险 |

证据：[Zotero reader][Z3]、[Zotero 虚拟列表][Z4]、[Zotero 焦点恢复][Z5]、[AFFiNE 分屏][A2]、[AFFiNE 拖动条][A5]、[RAGFlow 来源预览][R2]、[RAGFlow PDF][R3]、[RAGFlow 上传][R4]、[Paperless PDF][P3]、[Open WebUI 来源][O1]、[Open WebUI 来源详情][O2]、[Khoj 输入][K2]。

## 4. 十项最值得借鉴的交互

| # | 模式与来源 | PaperPilot 的具体应用 | 适配边界 |
|---|---|---|---|
| 1 | Zotero：选中条目联动详情 [Z1][Z2] | 单击文献显示片段详情，明确“打开阅读”；列表筛选和滚动保留 | 不实现未有接口支持的元数据编辑 |
| 2 | Zotero：阅读位置独立保存 [Z3] | 当前阶段记住 paperId/chunkId；未来 PDF 才记页码/缩放 | 页面状态不等于服务端保存 |
| 3 | Paperless：查询参数与保存视图恢复 [P2] | URL 保存标题查询、年份、状态和排序；返回文献库恢复位置 | 保存视图仅本地偏好，跨设备同步不在范围内 |
| 4 | RAGFlow：选择后出现批量操作条 [R1] | 选中后提供“用于回答/生成草稿”、清空；选择数和有效文献数可见 | 首轮不做批量移出，避免逐条删除被误认原子操作 |
| 5 | AFFiNE：视图与内容分离 [A1][A2] | 报告实体、编辑缓冲、活动面板分别建模；阅读/写作比例按项目恢复 | 不复制其自由分屏系统 |
| 6 | RAGFlow：来源携带上下文打开预览 [R2][R3] | 点击引用直接将 paperId/chunkId 送入主阅读区，保留回答 | 当前只有片段高亮；PDF 坐标是后续依赖 |
| 7 | Open WebUI：来源计数与渐进展开 [O1][O2] | 回答底部显示来源数，按需展开紧凑来源行；单条点击定位 | 保留 Mock、synthetic 与待核验标志 |
| 8 | Khoj：来源预览与完整清单分层 [K1] | 只预览少量来源，其余用“全部 N 条”；完整列表可复制 | 不默认向外站发送论文名称或来源 URL |
| 9 | Paperless：列表显示与筛选控制聚合 [P1] | 文献工具栏统一放标题搜索、年份、状态、排序、导入 | 只提供 title/year/status，不假设作者、主题和收藏 |
| 10 | Zotero/Paperless：虚拟化与资源释放 [Z4][P3] | 先本地分页，规模需要时虚拟化；切换资源取消读取并清理观察器 | 虚拟化无法解决接口全量下载，不能宣称服务端大库能力 |

## 5. PaperPilot 当前前端审查

### 5.1 路由与组件组织

| 位置 | 当前职责 | 观察 |
|---|---|---|
| `frontend/src/router.ts` | `/`、项目 papers/research/security，未知路由回首页 | 页面同步导入；没有独立 PDF、报告详情或会话路由 |
| `frontend/src/App.vue` | 登录恢复、health、侧栏/抽屉、项目选择、面包屑、RouterView | RouterView key 包含用户和 route.path；路径变化重建页面，查询参数变化则不会 |
| `pages/ProjectsPage.vue` | 身份进入、项目列表、创建及成员交互 | 项目是工作边界，不能将论文状态跨项目混用 |
| `pages/PapersPage.vue` | 单文件导入、年份过滤、文献表、详情抽屉、移出 | 上传面板常驻 275px；没有标题搜索、排序和多选 |
| `pages/ResearchPage.vue` | 检索、文献选择、片段阅读、问答、证据、报告编辑/版本 | 一个组件承载多种异步流程，函数与模板耦合较强 |
| `components/SourcesPanel.vue` | 折叠来源、摘录、页码、论文/片段 ID | 没有 emit 定位事件，不能直接让每条抽屉来源更新阅读区 |
| `components/WorkspaceSidebar.vue` | 项目导航、快捷项目、账号 | 顶部另有项目选择器；快捷项目跳到 papers，顶部选择器保持当前 section |
| `components/TaskRecoveryPanel.vue` | 本地任务记录、状态核对、明确重试 | 应保留可靠性语义，可压缩展示但不能移除未知提交入口 |
| `SecurityPage.vue`、`SecurityEvidencePanel.vue` | 查询与审计及安全证明 | 本轮不重构其业务；公共壳层变化需做回归 |

### 5.2 状态与 API 耦合

- 全局 `state.ts` 使用模块级 Vue ref/computed 保存身份、项目和运行模式，没有 Pinia。身份变化清理项目状态并改变请求会话代次。
- 页面局部 ref 保存文献选择、年份、问题、回答、证据、报告正文和版本。三种助手面板用 `v-show`，同页切换能保留内容；离开页面则依赖重新读取。
- `useResource.ts` 为只读资源保存 loading/loaded/error，有请求序号与卸载防迟到覆盖。每次 load 先清空 data，刷新时存在闪空/丢失可见内容的成本。
- `useTaskRecovery.ts` 将任务恢复记录按用户、项目和页面隔离到 sessionStorage；有指纹、幂等键、提交未知和查询恢复。任务轮询间隔 500ms，有界轮询；不能等价称为持续后台推送。
- `useAction.ts` 控制命令 busy/error；报告保存另保留同载荷幂等键。只读与写入状态不能为了统一 UI 简单合并。
- `api/client.ts` 基于 openapi-fetch 和生成 schema，处理会话/CSRF、统一错误及超时。`schema.d.ts` 是生成文件，不能手工改成方案需要的字段。
- 四份全局 CSS 依次引入，`.research-columns`、`.work-panel` 多处覆盖，最终布局依赖引入顺序。当前桌面侧栏 232px，研究三列最小宽分别 180/290/310px，另有列间距和面板 padding。

### 5.3 已有能力、缺口与契约边界

| 能力 | 当前情况 | 本轮可做的前端优化 |
|---|---|---|
| 上传 | 单 PDF、20 MiB 检查、PDF 文件头检查、202 任务及恢复 | 抽屉导入、明确阶段和重复提交阻止；不显示伪百分比 |
| 文献列表 | 服务端年份过滤、title/year/status；owner 可移出 | 本地标题/状态过滤、排序、分页、选择，标清当前已加载范围 |
| 检索 | keyword/semantic/hybrid + year 范围 + limit；当前页面固定 10 条 | 独立检索范围说明和空态；可在现有最大 50 内选结果数 |
| 问答/草稿 | question + paper_ids，最多 50 篇；当前逻辑默认取全部 searchable | 明确选择范围，提交前快照；空范围阻止，不发送空数组猜测语义 |
| AI 对话 | 单次 answer 结果 + 任务恢复 | 本地保留本次操作上下文；不承诺多轮服务端会话 |
| 阅读 | PaperDetail.sources，页码/摘录 | 片段阅读、引用回跳、摘录与快照对照 |
| PDF | OpenAPI 无原文件读取路径；Source 无 bbox/positions | 先显示明确不可用原因；不拼接后端内部存储路径 |
| 证据 | 提取任务、历史结果、五字段及不足状态 | 单文献直接读取已有证据；查看与重新提取分开 |
| 报告 | 正文/问题/来源快照、不可覆盖版本、base_version_id、文本导出 | 编辑保护、历史预览、保存冲突提示；不伪装自动保存 |
| 成员、安全 | 已有独立流程和权限契约 | 公共导航兼容，不变更权限或 A/C/D 模块 |

**范围关键限制**：SearchRequest 没有 paper_ids；QuestionRequest 没有 mode/year。不能把一个“范围选择器”宣称同时控制所有操作。本轮设计为两个清楚的标签：`片段检索：项目 + 年份 + 模式`，`生成材料：已选 N 篇`。如果未来需要选中文献内的服务端相关性检索，应另行提案；不能在前端过滤 top-10 后声称等价完成。

### 5.4 问题与可复现路径

说明：U01–U12 为源码可复现路径，除特别标注外，本轮未逐项自动化复现。准备条件：开发 Mock 运行，进入演示项目；涉及版本时先生成/手填正文并保存。

| 编号/优先级 | 问题与影响 | 操作路径与预期观察 | 代码依据 |
|---|---|---|---|
| U01 / P0 | 页内切换版本可能丢失编辑 | 保存 v1 → 修改正文但不保存 → 点击“版本 1”；正文直接变回 v1。打开已保存报告也会走该路径 | ResearchPage `openVersion` 直接赋值；仅路由 guard 检查 dirty |
| U02 / P0 | 检索过滤与生成范围不一致 | 填年份范围并检索 → 生成回答；观察请求：search 带年份/mode，answers 的 paper_ids 来自全部 searchable；选择某篇阅读也不限定生成 | ResearchPage `generate`；useTaskRecovery `api.papers(projectId)` |
| U03 / P1 | 跨页恢复不完整 | 研究页选第二篇、输入自定义问题、检索 → 文献 → 研究；重新挂载并默认选择第一篇，问题恢复默认值 | App RouterView key；ResearchPage 局部 ref/onMounted |
| U04 / P1 | 引用定位被抽屉遮挡，且多条来源不能逐条联动 | 回答引用 → 打开来源抽屉，底层阅读区同时切换；关闭抽屉才易查看；报告引用清单里展开第二条不会定位第二条 | `showSources` 仅自动定位第一条；SourcesPanel 无定位事件。单条基础联动本次测试通过 |
| U05 / P1 | 文献查找效率低 | 进入文献工作台 → 查标题、排序或选多篇；仅见年份范围与单条详情/移出 | PapersPage 表格未定义搜索、多选、排序 |
| U06 / P1 | 高频工作区被低频面板占据 | 文献页已导入后仍常驻导入栏；研究页同时出现标题说明、问题卡、任务区、流程条、片段/搜索/助手卡 | library-layout 与多层 work-panel；本次研究区截图确认卡片关系 |
| U07 / P1 | 报告研究问题与检索问题共享 | 打开已保存报告 → 改顶部问题用于另一轮搜索 → 报告 dirty 变动；保存将写入新的 research_question | 单一 `question` 同时用于 search、生成、editorDirty 和 save |
| U08 / P1 | 刷新会闪空，暂时失去上下文 | 文献/详情加载后 → 慢网下点重新加载；data 先清初始值，结果重新出现 | useResource.load 开始即清 data |
| U09 / P1 | 过滤无结果引导错误 | 用无匹配年份过滤文献；空态仍引导“导入一篇 PDF”，缺少一键清空筛选 | PapersPage 固定 el-empty 文案 |
| U10 / P1 | 窄屏把任务区纵向叠起 | 390px 打开研究页 → 问题、任务、文献选择、片段、检索、助手依次堆叠；问答与来源来回需滚动 | research-security.css 1000px 以下单列。本轮未运行窄屏测试 |
| U11 / P2 | 跳转入口的项目切换语义不一致 | 研究页从顶部切项目仍到研究；从侧栏快捷项目进入却到文献 | App.switchProject 对比 WorkspaceSidebar 快捷链接 |
| U12 / P1 | 资源规模与生成上限不匹配 | 项目有超过 50 篇 searchable 时生成，被前端拒绝并要求限定项目；页面无可直接限定生成材料的选择器 | useTaskRecovery 50 篇检查；PapersPage/ResearchPage 无生成多选 |

不应误判为缺失的能力：已经有错误重试、任务恢复、合成来源标志、证据不足、角色控制、报告引用快照、路由离开保护、助手 Tab 方向键与跳到主内容入口。重构应保留这些能力。

## 6. 优化后的信息架构

```text
工作空间
├─ 项目总览：项目与成员管理
└─ 当前项目（唯一项目选择入口）
   ├─ 文献：紧凑列表 / 筛选 / 选择生成材料 / 按需导入
   ├─ 研究：阅读区 + 助手区
   │  ├─ 阅读：片段 / 检索结果 / 引用定位与返回
   │  └─ 助手：问答 / 单文献证据 / 报告
   ├─ 报告入口：进入研究的报告模式与报告清单
   └─ 查询与审计：保持现有独立页面
公共状态条：任务数、失败/状态未知；完整任务恢复抽屉
```

“报告入口”指向研究页报告模式，复用编辑器，不再建一份报告实现。文献和研究各有不同任务，允许同时存在；同一文献库不在研究区额外重复完整筛选器。项目总览是返回入口，面包屑只表达位置。桌面只保留一个主导航；移动端菜单与项目快捷 Tab 不同时重复可见。

## 7. 两种完整布局方案

### 7.1 方案 A（推荐）：紧凑文献库 + 阅读/写作双区

桌面研究模式草图：

```text
┌ 项目导航 200 ┬ 项目 / 研究                      任务(1) ┐
│ 文献         ├ 最近打开：文献甲 | 报告乙*               │
│ 研究         ├ 文献列 200 ┬ 阅读区 ≥420 ┬ 助手区 ≥340   │
│ 报告         │ 紧凑文献行 │ 片段/检索   │ 问答 证据 报告 │
│ 查询与审计   │ 年份/状态  │ 引用定位    │ 当前模式内容   │
│              │ 当前选中  │ 返回前位置  │ 输入/保存操作  │
└──────────────┴───────────┴─────────────┴───────────────┘
```

尺寸是设计起点，按容器可用宽度而非仅窗口宽度降级：

- 研究内容宽度 ≥1000px：显示 200px 文献列、至少 420px 阅读区、至少 340px 助手区及分隔线；全局导航可折为 56px 图标栏，图标有名称/提示。
- 760–999px：文献列进入可呼出的面板，保留阅读/助手双区；不足以容纳最小宽时切单区。
- <760px：顶部保留“阅读 / 问答 / 证据 / 报告”局部 Tab，一次只显示一个主区；“返回来源前位置”和当前文献始终可达。移动端底部输入需避让软键盘和安全区。
- 桌面至多两条主内容滚动轨道（阅读和助手）；文献列独立滚动不带多层内部滚动。控件固定在所属面板边缘，不让整页长表把问答挤到页底。

文献模式使用整宽表格＋可关闭详情区，上传由工具栏打开抽屉。报告模式允许放大助手区，阅读区保留为证据对照；文献列默认收起。最近打开项首轮仅限文献和报告，建议最多 5 项，不做任意网页标签或跨设备会话。

交互路径：文献行“阅读”→ 研究页恢复该文献；回答来源→同屏定位片段；“加入报告”→报告 Tab 显示待保存标识。点击来源不强制打开抽屉，完整引用清单仍可按需打开。

### 7.2 方案 B：页面专注模式 + 按需来源面板

```text
┌ 项目导航 ┬ 项目 / 当前页面                     任务(1) ┐
│ 文献     ├ 文献 | 阅读 | 问答 | 证据 | 报告             │
│ 研究     ├───────────────────────────────────────────┤
│ 报告     │ 单个任务占据全宽主区                       │
│ 审计     │ 阅读页：文献选择 + 片段                     │
│          │ 问答页：问题 + 回答 + 来源行                 │
│          │ 报告页：清单/正文 + 保存                     │
└──────────┴───────────────────────────────────────────┘
                          来源点击 → 侧边面板 / 窄屏全屏
```

保留现有 papers/research/security 页面。research 以 query 参数的 mode 切换阅读/问答/证据/报告，组件状态提升到项目工作区，避免切换丢失。文献库同样紧凑化、上传抽屉化；问答或报告需要核对来源时才打开侧面板，关闭回原焦点。桌面来源面板可固定 400–480px，移动端全屏。报告为全宽编辑器，清单和历史版本按需面板。

该方案更适合单任务用户和小屏，但问答与阅读不能始终同时可见；必须提供一键回到上一任务。把顶部五项做为研究内部模式，不能又在主侧栏重复五个入口。

### 7.3 同口径比较

以下为设计路径估算，起点为已打开目标项目、目标文献可见；不计输入、网络等待与保存确认。信息密度是布局容量目标，非竞品实测。

| 比较项 | A：双区 | B：专注模式 |
|---|---|---|
| 找文献并阅读 | 文献行“阅读”1 次进入，保留列表状态 | 同为 1 次进入阅读模式 |
| 回答→来源→继续看回答 | 来源 1 次，同屏保留回答；窄屏需返回 1 次 | 来源打开 1 次、关闭 1 次 |
| 片段→加入报告→继续阅读 | 加入 1 次；桌面可同时对照 | 加入 1 次切报告，返回阅读另 1 次 |
| 文献库信息密度 | 默认 40–44px 行高，目标 900px 高桌面可见 ≥14 行 | 相同列表目标；阅读单任务空间更宽 |
| 并行信息密度 | 文献/来源/助手同时可见，更适合证据核对 | 同时内容少，页面本身更清爽 |
| 实现成本估计 | 约 10–15 人日（含关键保护/范围修复、回归，不含 PDF） | 约 7–10 人日（同等保护/范围修复与回归） |
| 维护复杂度 | 中：面板比例、焦点和多个 scroll 状态需统一 | 低至中：模式状态恢复和来源面板仍需统一 |
| 主要风险 | 中等屏幕拥挤、复杂分屏容易超范围 | 上下文反复打开/关闭，研究链路较长 |
| 适用条件 | 桌面研究、经常交叉核对证据 | 时间紧、小屏占比高、一次做一项任务 |

工期按一名前端熟悉现有项目、可用测试环境估算，重叠任务不重复累加；外部依赖和用户评审时间不包含。推荐 A，但采用受限双区而非 AFFiNE 式任意分屏。如果项目只允许一周，采用 B 并保留后续向 A 演进的状态模型。

## 8. 五类页面的具体方案

### 8.1 文献管理

- 单行工具栏：标题搜索、年份、状态、排序、导入。标题/状态/排序明确属于本地已加载列表；年份沿用 API。筛选徽标可逐个清除，另有清空全部。
- 表格默认列：复选框、标题、年份、处理状态、更多操作。默认标题两行以内；可选舒适密度。未知年份显示“未标注”，排序固定置后；标题排序保留 paper_id 作为稳定次序兜底。
- 行点击选中并显示详情；标题或“阅读”明确进入研究区，避免单击/双击语义互相冲突。复选框不触发行导航。
- 选择后出现“已选 N 篇 / 可用于生成 M 篇”，只允许 searchable 进入生成材料。过滤变化清除批量选择并提示，避免隐藏对象被操作。
- 首轮本地分页 50 行，选择仅当前结果页；跨页选择留后续。批量移出、元数据编辑、标签不在首轮。
- 空项目→导入；筛选无结果→清筛选；加载失败→重试；待处理→任务入口，各状态有不同引导。

### 8.2 阅读

- 标题栏显示文献名、状态、真实/合成标志。当前模式明确写“来源片段”，原文件不可用时显示说明。
- 片段选择用紧凑目录/列表代替大量横向页码按钮；一页多片段可区分，标题含页码和片段序号。
- 定位事件使用 projectId/paperId/chunkId 与来源快照，citation_id 只在所属回答/报告内用于展示，不能作为全局唯一键。
- 点击引用记录原文献、片段、阅读滚动和触发按钮；加载期间显示目标信息；找不到目标时展示引用快照和“当前片段不可用”，不得自动高亮第一段。
- 后续接 PDF 时才增加页码、缩放、文字搜索。没有坐标不能承诺精准框选；OCR 文本和 PDF 页文本不同不能宣称逐字命中。

### 8.3 AI 问答

- 问题输入与“生成材料 N 篇”同处；点击范围可查看明确文献名单。检索模式/年份放入“查找片段”，不与生成范围混在同一工具条。
- 问答问题 `questionInput` 与报告的 `reportResearchQuestion` 分开；用户主动从问题创建草稿时才复制，后续修改不静默影响已有报告。
- 单次回答显示“问题快照 + 本次材料范围 + 回答 + 来源数”。切换文献仅改变阅读上下文，不改已经提交任务的范围。
- 首轮使用现有非流式任务；显示排队/处理中/状态未知，不制造逐 token 流式动画。可以保留当前浏览器会话的结果，但不宣称后端多轮记忆。
- 超过 50 篇时提交前要求明确选择 ≤50；没有 searchable 时禁用并引导处理文献，不发送空范围。

### 8.4 证据

- 顶部明确“证据对象：文献 X”；选中文献即可“查看已有证据”，不要求先出现检索命中。
- 默认读已有结果；“重新提取”是独立明确写操作，有任务状态，切文献不自动提取。
- 五字段按行显示：研究问题、方法、结果、结论、局限；每行显示状态和支撑来源数，展开后可直接定位来源。
- 保留 not_mentioned / insufficient_evidence / pending_verification 差异；不把待核验设计为已经人工确认。现有 API 无核验写入接口，不提供虚假的“确认完成”持久状态。

### 8.5 报告

- 清单与编辑器复用一套状态。正文、标题、研究问题、引用快照构成一个编辑缓冲；报告 ID 与基线版本另存。
- 所有可能替换缓冲的动作（开报告、开版本、载入生成草稿、关闭工作项、切项目、离开页面）进入同一保护流程。
- 历史版本先只读预览，明确“以此版本继续编辑”，不能点击即覆盖缓冲。新版本保存继续使用 base_version_id，不通过去掉基线绕过冲突。
- 保存结果未知时保留载荷与幂等键；已确认保存后列表刷新失败只重读列表，不再次保存。版本冲突保留草稿并允许读取最新版本、人工比较。
- 默认纯文本/现有正文编辑器，引用作为快照独立维护；首轮不引入富文本协作、自动正文重排或自动修改历史引用编号。

## 9. 关键流程与状态规则

### 9.1 导入与任务

```text
选择文件 → 本地检查 → 待提交 → 提交中
  ├─ 明确拒绝 → 保留文件/标题，展示原因
  ├─ 取得 task_id → 排队/处理中 → 成功或失败
  └─ 未取得确认 → 提交结果未知 → 原请求核对
已知任务查询失败 → 当前状态未知 → 查询原任务
明确失败 → 用户明确重试原任务；不得当作重新导入
```

文件读取不是上传进度，202 不是处理成功；未有服务端百分比时只显示阶段。未知状态不能被新页面的空任务面板掩盖。sessionStorage 不可用时继续保留现有阻止不安全提交的语义。

### 9.2 检索、生成与引用

1. 文献列表维护可见过滤；生成材料维护独立明确选择。
2. 提交瞬间冻结 question、paperIds、任务种类；结果归属于该操作上下文。
3. 请求代次与用户/项目校验双重防迟到覆盖；离开项目只取消前端读取，不宣称取消服务端任务。
4. 引用定位先显示目标，再加载详情、匹配 chunk；成功高亮，失败保留快照。完成后提供返回原位置。
5. 非活动工作项的任务完成只增加状态标志，不强制切换当前报告或覆盖编辑；用户主动查看才应用。
6. 向报告添加来源按 paperId + chunkId + excerpt 去重，保留 synthetic 等来源属性；不借此声明论断已验证。

### 9.3 报告与导航

```text
只读版本 → 开始编辑 → dirty → 保存中
  ├─ 确认成功 → 新基线版本，dirty=false
  ├─ 保存未知 → 保留编辑/原请求，待核对
  └─ 冲突或失败 → 保留编辑，给出恢复动作
dirty + 替换/关闭 → 保存后继续 / 放弃后继续 / 留在当前项
```

只有收到保存成功确认才继续“保存后离开”；保存失败或未知留在编辑器。关闭浏览器可用 beforeunload 做尽力提醒，不能保证系统强制关闭时恢复；首轮不默认将论文正文持久化到 localStorage。

### 9.4 状态归属和 URL

| 状态 | 归属与保留 | 清理规则 |
|---|---|---|
| user、health、projects | 现有全局 state | 会话变化清理 |
| paperId/chunkId、活动模式、报告/版本标识 | URL query + 工作区状态；标识必须经 API 权限验证 | 项目/身份改变隔离，失权清理 |
| 标题查询、年份、状态、排序 | URL query；输入中的草案先局部保存 | 清筛选重置；浏览器前进后退可恢复 |
| 正文、完整问题和来源文本 | 内存编辑缓冲，不写 URL | 关闭前保护；退出登录清理 |
| panel 比例/密度 | 可版本化本地偏好，用户/项目隔离 | 过期或无效值回默认 |
| task 恢复 | 复用 sessionStorage 与现有幂等机制 | 沿用现有隔离和保留策略，不扩为无限历史 |

对 query 的读取与更新必须 watch，不能只在 setup 读取一次；同路由换文献不依靠重建页面。避免用无限 KeepAlive 缓存绕过状态设计。首轮采用 Vue composables + provide/inject 即可，不必为布局强行引入 Pinia。

## 10. P0 / P1 / P2 与建议实施顺序

以下都是待确认的实施提案，本轮没有执行代码修改。

| 优先级 | 工作包 | 完成条件/依赖 | 估算 |
|---|---|---|---|
| P0 | 编辑缓冲保护统一；页内版本/报告切换先预览 | U01 路径不丢编辑；生成迟到不覆盖 | 1–2 人日 |
| P0 | 明确检索范围与生成材料；允许选择 ≤50 篇；分离报告研究问题 | 请求与 UI 范围一致；不改 OpenAPI | 1–2 人日 |
| P1 | 抽离工作区、来源、报告状态；URL 恢复 | 返回和前进后退恢复；无跨项目串状态 | 2–3 人日 |
| P1 | 方案 A 壳层与文献表、导入抽屉、任务状态条 | 密度和窄屏规则通过；安全页不退化 | 2–3 人日 |
| P1 | 引用直接定位、返回、来源清单定位事件；证据入口 | 不用关闭抽屉才能看来源；无来源有正确说明 | 1–2 人日 |
| P1 | 键盘/焦点、错误/空态、关键流程回归 | 第 13 节验收通过 | 2–3 人日 |
| P2 | 最近工作项、比例恢复、列定制、必要时虚拟化 | 有规模/使用数据支持，不增自由分屏 | 另排 2–4 人日 |
| P2 / 外部依赖 | 完整 PDF、精确坐标高亮、真正多轮会话/跨设备恢复 | 需要另行接口与模块协作评审，本轮不实施 | 依赖澄清后估算 |

任务可交叉安排，表中简单相加不是承诺排期。首轮 P0 + P1 目标约 10–15 人日；若采用 B，可减少面板与状态布局工作。

## 11. 预计改动的前端文件和组件

| 文件/新增建议 | 变更目的 | 边界 |
|---|---|---|
| `frontend/src/App.vue` | 紧凑壳层、项目入口统一、query 变化不重置整个工作区 | 保留登录、health、401 与会话隔离 |
| `frontend/src/router.ts` | 懒加载页面、研究模式/query 约定，报告入口复用研究页 | 旧路径兼容，不改后端路由 |
| `frontend/src/components/WorkspaceSidebar.vue` | 消除重复入口，项目切换保持 section | 不变更角色权限 |
| `frontend/src/pages/PapersPage.vue` | 容器化列表、过滤、选择和导入 | 继续调用现有 API |
| `frontend/src/pages/ResearchPage.vue` | 由流程大组件变为模式/布局容器 | 保留现有任务协议 |
| 新 `components/workspace/WorkspaceLayout.vue`、`WorkspaceStatusBar.vue` | 受限双区、任务摘要 | 方案 B 可简化 Layout |
| 新 `components/library/PaperTable.vue`、`PaperFilterBar.vue`、`ImportDrawer.vue` | 列表信息密度、局部筛选、按需导入 | 无批量删除假接口 |
| 新 `components/reader/SourceReader.vue`、`SourceList.vue` | 来源定位、上下片段、回跳 | 当前只做片段阅读 |
| 新 `components/research/QuestionPanel.vue`、`GenerationScopePicker.vue`、`EvidenceInspector.vue` | 问题/材料范围和证据对象独立 | 不伪装 mode/year 传给生成接口 |
| 新 `components/reports/ReportEditor.vue`、`ReportList.vue`、`VersionPreview.vue` | 编辑缓冲、历史只读预览、冲突提示 | 保存原有 base_version_id 与来源快照 |
| `frontend/src/components/SourcesPanel.vue` | 增加明确定位事件、紧凑显示与标识详情折叠 | 不修改 Source Schema |
| 新 `composables/useWorkspace.ts`、`useReportEditor.ts`、`useSourceNavigation.ts` | 按用户/项目隔离状态与保护动作 | 没有必要立即增加第三方状态库 |
| `composables/useResource.ts` | 可选保留旧数据刷新/取消读取 | 仅同授权作用域保留；不同目标立即隔离，不泄漏旧来源 |
| `composables/useTaskRecovery.ts` | 接受显式生成材料、结果归属与非活动项提示 | 保留指纹、幂等键、未知提交与恢复逻辑 |
| `api/client.ts` | 可选增加只读请求取消能力 | 不改路径/载荷契约，不添加未声明端点 |
| `style.css`、`home-shell.css`、`business-workspace.css`、`research-security.css` | 合并重复布局规则，新组件样式局部化，保留主题变量 | Security 的规则原样迁移或隔离，勿全局删类 |
| 现有 `frontend/tests/connected-workspace.spec.ts`、`business-states.spec.ts`、`task-recovery.spec.ts` 等 | 按验收补充/调整关键行为测试 | 实施获确认后才改测试代码 |

不计划修改：`backend/**`、数据库迁移、`contracts/openapi.json`、自动生成的 `frontend/src/api/schema.d.ts`，以及 A/C/D 成员实现。`SecurityPage.vue` 与 `SecurityEvidencePanel.vue` 不做业务重构，只进行共享样式和导航兼容验证。

## 12. 技术风险、兼容性与许可证

### 12.1 技术与兼容性

| 风险 | 处理原则 |
|---|---|
| 旧链接和 query 状态漂移 | 保留现有 URL；解析 query 时校验枚举、年份、UUID，未知值回退；前进后退作为正式流程 |
| 请求迟到、旧项目缓存泄漏 | 缓存 key 至少含 userId/projectId/资源标识；沿用请求序号和会话代次；401/403/404 清理受保护内容 |
| 新面板切换绕过脏数据保护 | 所有替换动作调用同一编辑器协调器；外部路由守卫只是其中一个入口 |
| 刷新保留数据造成来源误认 | 仅同一资源允许保留；切 paperId/chunkId 立即取消旧高亮，加载中显示目标来源 |
| 全量列表规模 | 本地分页降低 DOM，不减少下载；大库服务端分页/检索需独立提案，不能擅自改 A 的接口 |
| 筛选后排序和计数失真 | 分开“已加载总数”“当前结果”“已选生成数”；年份未知固定规则；结果上限有说明 |
| 任务被 UI 隐藏后误判完成 | 紧凑状态条保留未知/失败，完整恢复操作可达；不增加服务端取消能力 |
| CSS 全局覆盖 | 先梳理引入顺序，逐组件迁移；Element Plus 覆盖限定工作区根类，回归项目/安全页 |
| Element Plus 版本能力差异 | 以 package-lock 锁定版本核对；先用已有 Drawer/Table/Input/Tabs，分隔条可自建小组件，不在研究阶段依赖未确认组件 |
| 新依赖与 PDF 体积 | 当前不增 PDF 库；后续先核对 worker/CSP/CMaps/中文字体/扫描件与授权文件获取，再懒加载 |
| 多设备/辅助技术 | 后续验收 Chromium/Edge、Firefox；Safari/iOS 无本轮环境，应明确补测责任，不宣称已兼容 |
| 文本渲染安全与隐私 | 保留 Vue 文本转义；若后续加 Markdown 必须处理原始 HTML/危险链接；不把论文正文/完整问题写进 URL，不直接复制竞品外站资源请求 |
| 浏览器 API | 当前已有 AbortSignal.any/timeout；新代码不能扩大未兼容浏览器范围，需按实际支持矩阵决定回退 |

性能策略按顺序实施：减少同步首包页面、避免进入研究页反复读取同一项目列表、同作用域缓存/失效、降低 DOM 数量，最后再根据测量引入虚拟化。没有竞品同硬件基准，不能声称某竞品更快。本轮未测首包体积或 LCP；下面的性能数值都是拟验收目标。

### 12.2 许可证检查结果

核对的是固定提交内的许可证文本。以下为工程复用边界检查，不表示对任意派生/分发场景完成法律审查。

| 项目 | 核对结果 | 本提案的处理 |
|---|---|---|
| Zotero Web Library | COPYING 明确为 AGPLv3，另有商标说明 [ZL] | 学习交互，独立实现 Vue 组件；不复制其源码、样式或品牌素材 |
| AFFiNE | 根 LICENSE 指定目录差异；所研究前端目录属于 MIT 范围，backend/common/native 有单独条款 [AL] | 不把整库统称 MIT；本轮不复制代码，后续如复用需逐文件/依赖核对并保留通知 |
| RAGFlow | 根 LICENSE 为 Apache-2.0，所读 PDF drawer 文件也有对应头 [RL] | 借鉴来源携带定位上下文；如未来复用需保留许可/通知、标记改动，并单查其 PDF 依赖许可 |
| Paperless-ngx | LICENSE 为 GPLv3 文本 [PL] | 独立实现列表和状态规则；不把 Angular 组件移植进闭源代码而忽略义务 |
| Open WebUI | 当前根 LICENSE 为自定义 Open WebUI License，含品牌条件；LICENSE_NOTICE 根据贡献时间区分历史许可 [OL][ON] | 不能按历史 MIT 简化整个当前仓库；不复制组件、logo、favicon 或换名模板 |
| Khoj | 根 LICENSE 为 AGPLv3 文本 [KL] | 参考来源渐进展示，不复制 React 组件或素材 |

建议在实现 PR 记录“设计参考链接、独立实现、实际新增依赖及许可证”。本报告没有将竞品代码引入项目。许可证未来可能改变，因此任何实际复用应再次按拟使用提交和文件复核。

## 13. 可执行的交互验收标准

下列是正式实现后的验收清单，不能与第 2 节本次已通过的两项测试混淆。测试数据均用隔离 Mock 或可控网络拦截，不依赖正式数据库写入。

| 编号 | 前置与操作 | 可观察的通过标准 |
|---|---|---|
| AC01 编辑保护 | 保存 v1，修改正文，点击历史版本/另一报告 | 不覆盖缓冲；出现预览或处理编辑选择；取消后正文逐字相同 |
| AC02 保存后继续 | dirty 状态选择保存后离开，分别注入成功、500 和确认超时 | 成功后才切换；失败/未知留在原编辑器，正文/基线/幂等上下文仍在 |
| AC03 范围正确 | 有 3 篇 searchable，选择其中 2 篇生成回答/草稿 | POST 的 paper_ids 恰为这 2 篇；界面显示同一材料快照，无未选文献 |
| AC04 范围边界 | 0 篇、51 篇选择、选择包含 processing 文献 | 0 篇/超过 50 禁止提交，解释原因；processing 不悄悄加入材料 |
| AC05 检索范围表达 | 设年份与 semantic，再生成回答 | search 请求含年份/mode；生成界面不声称沿用这些参数，明确显示文献名单 |
| AC06 问题隔离 | 打开报告，修改检索问题，不操作报告问题 | 报告研究问题与 dirty 基线不受检索输入影响 |
| AC07 引用联动 | 回答点击文献 B 的第二片段引用 | A 桌面一次点击更新阅读区、回答保留可见；paperId/chunkId 正确；来源 ID 重号不串段 |
| AC08 来源缺失 | 响应中的 chunkId 不在当前详情 | 显示缺失与原引用快照，不高亮其他段落、不允许把其他段冒充该引用 |
| AC09 返回位置 | 文献 A 滚动并选片段 → 从回答跳文献 B → 返回 | 恢复 A 的片段和滚动位置；焦点返回原引用按钮 |
| AC10 抽屉多来源 | 报告清单打开并点击第二条来源的定位 | 阅读区定位第二条，不局限第一次传入的来源；窄屏定位后可返回清单 |
| AC11 列表恢复 | 设置标题/年份/状态/排序，打开阅读后返回；再用浏览器前进后退 | query、选中行、当前页与滚动恢复；非法 query 有安全默认值 |
| AC12 空错状态 | 空项目、过滤无结果、详情 500、文献 404 分别进入 | 分别出现导入、清筛选、重试、资源不可用；错误不显示为“没有文献” |
| AC13 未知提交 | 上传请求发送后截断确认，再点击操作 | 不创建第二个请求；保留原指纹/幂等键并提供核对；有 taskId 时只查询原任务 |
| AC14 非活动结果 | 生成报告后改正文或切其他工作项，再释放迟到成功响应 | 不覆盖正文/当前活动项；出现完成待查看标志；用户主动载入经过保护 |
| AC15 快速切换 | 延迟 A 详情，先后选 A/B，再释放 A | 最终只显示 B；不会把 A 文本放入 B 标题下；切项目/退出登录后旧数据不出现 |
| AC16 证据入口 | 从选中文献直接看已有证据，再点重新提取 | 查看只 GET；提取才 POST；不足、未提及、待核验保持区分 |
| AC17 报告一致性 | 同一来源加入两次，保存、重开、查看旧版本、导出 | 去重，页码/摘录/synthetic 保留；旧版本不被新编辑污染；导出对应当前已保存版本 |
| AC18 密度 | 1440×900 文献页，至少 30 条短标题数据 | 工具栏可见，默认可见至少 14 行；没有常驻导入大卡，标题长时不遮挡状态/操作 |
| AC19 窄屏 | 390×844 与 768×1024，打开阅读/问答/报告、来源和菜单 | 页面无横向溢出；必要表格滚动限定表格内；只有一个主活动区，输入/保存不被遮住 |
| AC20 键盘 | 不用鼠标完成导航、选择文献、切助手 Tab、定位引用、关闭面板、保存 | 按钮可聚焦且有名称；Tab 顺序合理；左右/Home/End 切 Tab；Escape 关闭临时面板并回原焦点；编辑保护框不能被误关导致丢稿 |
| AC21 可访问性 | 键盘和读屏检查加载、失败、任务完成；检查主要文字与选中状态 | 状态用适度 live region，错误可关联字段；选中/失败不只靠颜色；焦点可见；触控目标目标值 ≥44px，不为密度缩小实际触控区 |
| AC22 性能 | 同一机器 Chromium，1000 篇本地合成元数据；预热后筛选/排序各做 10 次 | 记录浏览器版本和配置，交互至绘制 p95 ≤200ms；分页仅渲染约当前 50 行，网络耗时另记；不将 Mock 结果称正式大库性能 |
| AC23 资源释放 | 反复开关阅读/来源 20 次、离开项目，再观察请求/监听器 | 没有旧项目轮询继续写 UI；无持续增加的观察器/定时器；单任务最多一个活动轮询 |
| AC24 兼容回归 | 执行现有身份、任务恢复、引用、报告、安全页关键测试 | 旧 URL 可用，401 正确清理，安全查询/审计语义和现有权限不改变 |

建议自动化覆盖 AC01–AC17 与关键窄屏流程；焦点、读屏、文字对比度、触控与软键盘需要人工复核。实施时测试真实危险状态，而不是为每个抽离组件堆砌镜像测试。

## 14. 评审决定与下一阶段入口

建议评审批准：方案 A 的受限双区；先完成 P0，再执行 P1；PDF、自由多分屏、富文本协作、持久多轮会话另列提案。若选 B，P0、来源模型、范围规则与报告保护完全沿用。

本轮仅新增这份研究报告及运行既有隔离测试产生的输出，没有修改生产代码、测试代码、数据库、API 契约或 A/C/D 模块。**正式实现必须先获得用户对方案和范围的明确确认。**

## 附录：固定提交的源码与许可证来源

以下均为实际读取文件。行范围仅指本次快照中的关键区域，可通过链接审查；不是 README 或宣传截图推导。

- [Z1] Zotero `library.jsx`：约 139–201 行定义 reader/library 切换、侧区、列表与详情。
- [Z2] Zotero `common/table.jsx`：列宽/排序/选中参数及约 284–302 行表格 ARIA。
- [Z3] Zotero `reader.jsx`：阅读页索引、阅读状态与附件加载。
- [Z4] Zotero `react-window-list.jsx`：约 5–20 行越界保护、24–65 行滚动恢复。
- [Z5] Zotero `modal-focus-restore.js`：弹层焦点恢复辅助。
- [A1] AFFiNE `workbench.ts`：视图状态、打开与比例调整。
- [A2] AFFiNE `split-view.tsx`：分屏拖放及面板渲染。
- [A3] AFFiNE `virtualized-list.tsx`：约 65 行起虚拟页面列表。
- [A4] AFFiNE `root-app-sidebar/index.tsx`：约 205–210 行快速搜索入口。
- [A5] AFFiNE `resize-handle.tsx`：拖动调整尺寸。
- [R1] RAGFlow `dataset/index.tsx`：约 85–102 行选择、145–203 行过滤/批量/表格。
- [R2] RAGFlow `pdf-drawer/index.tsx`：约 30–72 行来源文档和高亮传递。
- [R3] RAGFlow `pdf-preview.tsx`：PDF 加载错误与 highlighter。
- [R4] RAGFlow `use-upload-document.ts`：文件上传、解析资格与部分成功。
- [P1] Paperless `document-list.component.html`：列、排序、视图、筛选、批量和加载。
- [P2] Paperless `document-list.component.ts`：约 265–315 行更新订阅、保存视图和 query 状态。
- [P3] Paperless `pdf-viewer.component.ts`：页码、搜索、加载销毁和 ResizeObserver。
- [P4] Paperless `document-detail.component.html`：详情操作、版本与阅读区域。
- [O1] Open WebUI `Citations.svelte`：约 171–245 行来源数展开和来源按钮。
- [O2] Open WebUI `CitationModal.svelte`：来源片段归并、排序与详情。
- [O3] Open WebUI `Sidebar/SearchInput.svelte`：会话筛选建议和搜索状态。
- [K1] Khoj `referencePanel.tsx`：约 602–730 行移动 teaser、完整 Sheet 与复制来源。
- [K2] Khoj `chatInputArea.tsx`：输入、研究模式、上传与错误。
- [K3] Khoj `chatSidebar.tsx`：聊天配置与移动宽度分支。

[Z1]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/src/js/component/library.jsx
[Z2]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/src/js/component/common/table.jsx
[Z3]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/src/js/component/reader.jsx
[Z4]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/src/js/component/common/react-window-list.jsx
[Z5]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/src/js/common/modal-focus-restore.js
[ZL]: https://github.com/zotero/web-library/blob/4cfa48994ab31c67c9fe10311c4abe57c0882188/COPYING
[A1]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/packages/frontend/core/src/modules/workbench/entities/workbench.ts
[A2]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/packages/frontend/core/src/modules/workbench/view/split-view/split-view.tsx
[A3]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/packages/frontend/core/src/components/page-list/virtualized-list.tsx
[A4]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/packages/frontend/core/src/components/root-app-sidebar/index.tsx
[A5]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/packages/frontend/core/src/modules/workbench/view/split-view/resize-handle.tsx
[AL]: https://github.com/toeverything/AFFiNE/blob/8cef09e1dd62b47a3cbc9f352aa10b3a90c75d34/LICENSE
[R1]: https://github.com/infiniflow/ragflow/blob/97b08d6585ee51757ea3fdd28e0a6a1473b2abfc/web/src/pages/dataset/dataset/index.tsx
[R2]: https://github.com/infiniflow/ragflow/blob/97b08d6585ee51757ea3fdd28e0a6a1473b2abfc/web/src/components/pdf-drawer/index.tsx
[R3]: https://github.com/infiniflow/ragflow/blob/97b08d6585ee51757ea3fdd28e0a6a1473b2abfc/web/src/components/document-preview/pdf-preview.tsx
[R4]: https://github.com/infiniflow/ragflow/blob/97b08d6585ee51757ea3fdd28e0a6a1473b2abfc/web/src/pages/dataset/dataset/use-upload-document.ts
[RL]: https://github.com/infiniflow/ragflow/blob/97b08d6585ee51757ea3fdd28e0a6a1473b2abfc/LICENSE
[P1]: https://github.com/paperless-ngx/paperless-ngx/blob/c21bdab5c88ab0624fcde787049436598ece9e85/src-ui/src/app/components/document-list/document-list.component.html
[P2]: https://github.com/paperless-ngx/paperless-ngx/blob/c21bdab5c88ab0624fcde787049436598ece9e85/src-ui/src/app/components/document-list/document-list.component.ts
[P3]: https://github.com/paperless-ngx/paperless-ngx/blob/c21bdab5c88ab0624fcde787049436598ece9e85/src-ui/src/app/components/common/pdf-viewer/pdf-viewer.component.ts
[P4]: https://github.com/paperless-ngx/paperless-ngx/blob/c21bdab5c88ab0624fcde787049436598ece9e85/src-ui/src/app/components/document-detail/document-detail.component.html
[PL]: https://github.com/paperless-ngx/paperless-ngx/blob/c21bdab5c88ab0624fcde787049436598ece9e85/LICENSE
[O1]: https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/src/lib/components/chat/Messages/Citations.svelte
[O2]: https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/src/lib/components/chat/Messages/Citations/CitationModal.svelte
[O3]: https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/src/lib/components/layout/Sidebar/SearchInput.svelte
[OL]: https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/LICENSE
[ON]: https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/LICENSE_NOTICE
[K1]: https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/interface/web/app/components/referencePanel/referencePanel.tsx
[K2]: https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/interface/web/app/components/chatInputArea/chatInputArea.tsx
[K3]: https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/interface/web/app/components/chatSidebar/chatSidebar.tsx
[KL]: https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/LICENSE
