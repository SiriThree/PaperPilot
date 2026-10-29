<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, type Models } from '../api/client'
import { useTaskRecovery } from '../composables/useTaskRecovery'
import TaskRecoveryPanel from '../components/TaskRecoveryPanel.vue'
import { useAction } from '../composables/useAction'
import { useResource } from '../composables/useResource'
import Feedback from '../components/Feedback.vue'
import SourcesPanel from '../components/SourcesPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import { yearProblem } from '../validation'
import { aiMock } from '../state'
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
const question = ref('检索增强生成如何核验证据？'), mode = ref<Models['SearchRequest']['mode']>('hybrid')
const yearFrom = ref<number>(), yearTo = ref<number>()
const yearError = computed(() => yearProblem(yearFrom.value, yearTo.value))
const searchResource = useResource<Models['SearchHit'][]>([], async () => (await api.search(pid, { query: question.value, mode: mode.value, year_from: yearFrom.value, year_to: yearTo.value, limit: 10 })).items)
const hits = searchResource.data
const answer = ref<Models['AnswerResult'] | null>(null), evidence = ref<Models['EvidenceResult'] | null>(null)
const evidenceNotice = ref('')
const evidencePanel = ref<HTMLElement | null>(null)
const assistantTab = ref<'answer' | 'evidence' | 'report'>('report')
function moveAssistantTab(event: KeyboardEvent, current: 'answer' | 'evidence' | 'report') {
  const tabs = ['answer', 'evidence', 'report'] as const
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (tabs.indexOf(current) + (event.key === 'ArrowRight' ? 1 : 2)) % 3
  assistantTab.value = tabs[index]!
  ;((event.currentTarget as HTMLElement).parentElement?.querySelectorAll('button')[index] as HTMLButtonElement | undefined)?.focus()
}
const selectedPaperId = ref(''), activeChunkId = ref(''), requestedChunkId = ref(''), activeCitation = ref('')
const library = useResource<Models['Paper'][]>([], async () => (await api.papers(pid)).items)
const sourceResource = useResource<Models['PaperDetail'] | null>(null, () => api.paper(pid, selectedPaperId.value))
const activeSource = computed(() => sourceResource.data.value?.sources.find(value => value.chunk_id === activeChunkId.value))
async function selectPaper(paperId: string, chunkId = '', citation = '') {
  selectedPaperId.value = paperId; requestedChunkId.value = chunkId; activeChunkId.value = ''; activeCitation.value = citation
  if (await sourceResource.load()) {
    if (selectedPaperId.value !== paperId) return
    const sources = sourceResource.data.value?.sources || []
    activeChunkId.value = (chunkId ? sources.find(value => value.chunk_id === chunkId) : sources[0])?.chunk_id || ''
    if (!citation) activeCitation.value = sources.find(value => value.chunk_id === activeChunkId.value)?.citation_id || ''
  }
}
function chooseSource(source: Models['Source']) { requestedChunkId.value = source.chunk_id; activeChunkId.value = source.chunk_id; activeCitation.value = source.citation_id }
const leaveOpen = ref(false)
let leavePromise: Promise<boolean> | null = null, resolveLeave: ((value: boolean) => void) | null = null
function finishLeave(value: boolean) { leaveOpen.value = false; resolveLeave?.(value); resolveLeave = null; leavePromise = null }
function guardEditor() {
  if (!editorDirty.value) return true
  assistantTab.value = 'report'; leaveOpen.value = true
  if (!leavePromise) leavePromise = new Promise<boolean>(resolve => { resolveLeave = resolve })
  return leavePromise
}
onBeforeRouteLeave(guardEditor)
onBeforeRouteUpdate(to => String(to.params.projectId || '') !== pid ? guardEditor() : true)
const selectedSources = ref<Models['Source'][]>([]), sourceDrawer = ref(false)
const body = ref(''), reportTitle = ref('研究综述草稿'), reportSources = ref<Models['Source'][]>([]), currentVersion = ref<Models['ReportVersion'] | null>(null)
const editorDirty = computed(() => currentVersion.value
  ? body.value !== currentVersion.value.body || reportTitle.value !== currentVersion.value.title || question.value !== currentVersion.value.research_question || JSON.stringify(reportSources.value) !== JSON.stringify(currentVersion.value.sources)
  : !!body.value.trim())
const notice = ref(''), historyReportId = ref<string | null>(null)
const reportResource = useResource<Models['ReportVersion'][]>([], async () => (await api.reports(pid)).items)
const historyResource = useResource<Models['ReportVersion'][]>([], async () => historyReportId.value ? (await api.versions(pid, historyReportId.value)).items : [])
const reports = reportResource.data, history = historyResource.data
let pendingSave: { payload: string; key: string } | null = null
let restoring = true
const incomingDraft = ref<Models['DraftResult'] | null>(null)
const editorSnapshot = () => JSON.stringify([body.value, reportTitle.value, currentVersion.value?.version_id])
let draftBaseline: string | null = editorSnapshot()
function applyDraft(result: Models['DraftResult']) {
  body.value = result.body; reportSources.value = result.sources; currentVersion.value = null; historyReportId.value = null; historyResource.reset()
  incomingDraft.value = null
  if (restoring) question.value = ''
  notice.value = `${result.mock ? 'Mock ' : '适配器返回的'}草稿已读取，尚未保存；请核对研究问题后保存。`
}
async function queryTask(id?: string) {
  // Explicitly opening another retained result is a user action, unlike a late worker response.
  if (id && id !== tasks.selected.value) draftBaseline = null
  await tasks.refresh(id)
}
const tasks = useTaskRecovery(pid, 'research', current => {
  const result = current.result
  if (result?.kind === 'answer') { answer.value = result; assistantTab.value = 'answer' }
  if (result?.kind === 'evidence') { evidence.value = result; evidenceNotice.value = ''; assistantTab.value = 'evidence' }
  if (result?.kind === 'draft') {
    if (draftBaseline !== null && editorSnapshot() !== draftBaseline) incomingDraft.value = result
    else applyDraft(result)
    draftBaseline = null
  }
})
onMounted(async () => { await Promise.all([reportResource.load(), library.load()]); const first = library.data.value.find(paper => paper.status === 'searchable') || library.data.value[0]; if (first) await selectPaper(first.paper_id) })
async function search() { if (!yearError.value && !searchResource.loading.value) await searchResource.load() }
async function generate(kind: 'answer' | 'draft') {
  if (tasks.blocked.value) return
  restoring = false
  if (kind === 'draft') { draftBaseline = editorSnapshot(); incomingDraft.value = null; assistantTab.value = 'report' }
  await tasks.submit({ kind, question: question.value })
}
async function extract(paperId: string) {
  restoring = false
  await tasks.submit({ kind: 'evidence', paperId })
}
async function viewEvidence(paperId: string) {
  if (tasks.blocked.value) return
  await run(async () => {
    const result = await api.evidenceHistory(pid, paperId)
    evidence.value = result.items[result.items.length - 1] || null
    assistantTab.value = 'evidence'
    evidenceNotice.value = evidence.value ? '已读取该文献最近保存的证据，未重新提取。' : '当前文献尚未保存结构化证据，可使用“提取证据”创建任务。'
    if (evidence.value) { await nextTick(); evidencePanel.value?.scrollIntoView({ block: 'start' }) }
  })
}
async function reconcile() {
  const item = tasks.record.value
  if (!item) return
  restoring = false
  await tasks.submit({ kind: item.kind, question: question.value, paperId: item.paperId }, true)
}
function showSources(sources: Models['Source'][]) { selectedSources.value = sources; sourceDrawer.value = true; const first = sources[0]; if (first) void selectPaper(first.paper_id, first.chunk_id, first.citation_id) }
function addSourceToReport() {
  const source = activeSource.value
  if (!source) return
  assistantTab.value = 'report'
  const existing = reportSources.value.find(value => value.paper_id === source.paper_id && value.chunk_id === source.chunk_id && value.excerpt === source.excerpt)
  if (existing) { notice.value = `该来源已在引用清单 [${existing.citation_id}] 中，未重复添加。`; return }
  if (reportSources.value.length >= 100) { notice.value = '当前报告已有 100 条引用，请先整理引用清单。'; return }
  let number = 1
  while (reportSources.value.some(value => value.citation_id === String(number))) number++
  const snapshot = { ...source, citation_id: String(number) }
  reportSources.value = [...reportSources.value, snapshot]
  body.value = `${body.value}${body.value ? '\n\n' : ''}${source.excerpt} [${number}]`
  notice.value = `来源 [${number}] 已加入正文和引用快照，仍待人工核验，尚未保存。`
}
async function save() {
  await run(async () => {
    const payload = { title: reportTitle.value, research_question: question.value, body: body.value, sources: reportSources.value, report_id: currentVersion.value?.report_id, base_version_id: currentVersion.value?.version_id }
    const snapshot = JSON.stringify(payload)
    if (pendingSave?.payload !== snapshot) pendingSave = { payload: snapshot, key: crypto.randomUUID() }
    currentVersion.value = await api.save(pid, payload, pendingSave.key)
    pendingSave = null
    notice.value = currentVersion.value.mock ? `已保存 Mock 版本 ${currentVersion.value.number}，后端重启后丢失。` : `已保存适配器返回的版本 ${currentVersion.value.number}；重启恢复需由 A 验收。`
    historyReportId.value = currentVersion.value.report_id
    // These are independent reads after an acknowledged save, never a reason to save again.
    await Promise.all([historyResource.load(), reportResource.load()])
  })
}
async function openReport(report: Models['ReportVersion']) { await run(async () => { historyReportId.value = report.report_id; if (await historyResource.load()) { const latest = history.value[history.value.length - 1]; if (latest) openVersion(latest) } }) }
function openVersion(version: Models['ReportVersion']) { assistantTab.value = 'report'; currentVersion.value = version; body.value = version.body; reportTitle.value = version.title; question.value = version.research_question; reportSources.value = version.sources; notice.value = `当前查看版本 ${version.number}` }
const expandedEvidence = ref<string[]>(['research_question', 'method'])
const evidenceStatusLabels: Record<string, string> = { pending_verification: '待人工核验', not_mentioned: '文献未提及', insufficient_evidence: '证据不足' }
const modeDescriptions = { keyword: '按关键词查找相关片段', semantic: '按研究问题的含义检索', hybrid: '结合关键词与语义关联' }
const evidenceLabels = { research_question: '研究问题', method: '研究方法', results: '实验结果', conclusion: '主要结论', limitations: '研究局限' } as const
</script>
<template>
  <div class="business-page research-page">
    <div class="business-heading"><div><span class="eyebrow">EVIDENCE & SYNTHESIS</span><h1>研究工作台</h1><p>带着问题阅读，让见解与来源保持关联。</p></div><div class="workspace-principle"><AppIcon name="quote" :size="19" /><span>先找到证据<br><strong>再形成结论</strong></span></div></div>
    <Feedback :busy="busy" :error="error" />
    <section class="work-panel research-composer">
      <label class="composer-label" for="research-question"><AppIcon name="search" :size="19" />你想研究什么？</label>
      <el-input id="research-question" v-model="question" type="textarea" :rows="2" maxlength="2000" placeholder="输入研究问题" aria-label="研究问题" />
      <div class="research-controls"><div class="search-options"><el-radio-group v-model="mode" aria-label="检索模式"><el-radio-button value="keyword">关键词</el-radio-button><el-radio-button value="semantic">语义</el-radio-button><el-radio-button value="hybrid">组合</el-radio-button></el-radio-group><div class="research-year-range"><el-input-number v-model="yearFrom" placeholder="起始年份" aria-label="检索起始年份" :min="1000" :max="3000" :controls="false" /><span>—</span><el-input-number v-model="yearTo" placeholder="结束年份" aria-label="检索结束年份" :min="1000" :max="3000" :controls="false" /></div></div><el-button type="primary" :disabled="!question.trim() || !!yearError || busy || searchResource.loading.value || tasks.blocked.value" @click="search"><AppIcon name="search" :size="16" />检索</el-button></div>
      <p v-if="yearError" class="field-error" role="alert">{{ yearError }}</p>
      <div class="generation-toolbar"><span>{{ modeDescriptions[mode] }}<small>{{ aiMock ? '当前为 Mock 演示' : 'AI 结果需人工核验' }}</small></span><div><el-button :disabled="!question.trim() || busy || tasks.blocked.value" @click="generate('answer')"><AppIcon name="quote" :size="15" />{{ aiMock ? '生成 Mock 回答' : '生成回答' }}</el-button><el-button :disabled="!question.trim() || busy || tasks.blocked.value" @click="generate('draft')"><AppIcon name="file" :size="15" />{{ aiMock ? '生成 Mock 草稿' : '生成草稿' }}</el-button></div></div>
    </section>
    <TaskRecoveryPanel :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="queryTask" @reconcile="reconcile" @retry="tasks.retryFailed" />
    <ol class="research-flow" aria-label="研究工作流"><li><b>01</b>选择文献</li><li><b>02</b>核对来源摘录</li><li><b>03</b>引用到报告 <small>仍待人工核验</small></li></ol>
    <div class="research-columns connected-workspace">
      <section class="work-panel literature-rail" aria-label="研究文献列表"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="book" /></span><div><h2>项目文献</h2><p>{{ library.data.value.length }} 篇 · 按项目限定范围</p></div></div>
        <Feedback :busy="library.loading.value" :error="library.error.value" retry-label="重新读取项目文献" @retry="library.load" />
        <el-select v-if="library.data.value.length" class="compact-paper-picker" :model-value="selectedPaperId" aria-label="选择研究文献" @change="selectPaper"><el-option v-for="paper in library.data.value" :key="paper.paper_id" :value="paper.paper_id" :label="paper.title" /></el-select>
        <div class="paper-list-items"><button v-for="paper in library.data.value" :key="paper.paper_id" class="research-paper" :class="{ 'is-selected': selectedPaperId === paper.paper_id }" :aria-pressed="selectedPaperId === paper.paper_id" :aria-label="`选择文献 ${paper.title}`" @click="selectPaper(paper.paper_id)"><strong>{{ paper.title }}</strong><span>{{ paper.year ?? '年份未标注' }}<small :class="`paper-state-${paper.status}`">{{ { searchable: '可检索', pending: '待处理', processing: '处理中', failed: '处理失败' }[paper.status] }}</small></span></button></div>
        <el-empty v-if="library.loaded.value && !library.data.value.length" description="项目尚无文献，请先导入" :image-size="48" />
      </section>
      <div class="research-reading">
        <section class="work-panel source-reader" aria-label="当前来源阅读区"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="quote" /></span><div><h2>{{ sourceResource.data.value?.paper.title || '来源摘录' }}</h2><p>以摘录和页码定位 · 原文仍需人工核对</p></div></div>
          <Feedback :busy="sourceResource.loading.value" :error="sourceResource.error.value" retry-label="重新读取来源" @retry="selectPaper(selectedPaperId, requestedChunkId, activeCitation)" />
          <template v-if="sourceResource.data.value"><div class="source-tabs"><button v-for="source in sourceResource.data.value.sources" :key="source.chunk_id" :aria-pressed="activeChunkId === source.chunk_id" :class="{ 'is-selected': activeChunkId === source.chunk_id }" @click="chooseSource(source)">第 {{ source.page_number }} 页 · [{{ source.citation_id }}]</button></div>
            <template v-if="activeSource"><p class="reader-location">第 {{ activeSource.page_number }} 页 · {{ activeSource.synthetic ? '合成来源' : '来源摘录' }} · 待核验</p><blockquote class="reader-excerpt" :data-active-chunk="activeSource.chunk_id" data-testid="active-source"><mark>{{ activeSource.excerpt }}</mark><span class="reader-citation">[{{ activeCitation || activeSource.citation_id }}]</span></blockquote><div class="reader-actions"><el-button type="primary" @click="addSourceToReport"><AppIcon name="quote" :size="14" />添加来源到报告</el-button><span>待人工核验</span></div><small class="reader-boundary">当前显示来源摘录，不代表已加载 PDF 或完成证据核验。</small></template>
            <el-empty v-else :description="sourceResource.data.value.paper.status === 'failed' ? '文献处理失败，暂无来源。可在任务区域查询原因并明确重试。' : sourceResource.data.value.sources.length ? '引用片段已不在当前文献中，请对照引用快照核验，未自动替换为其他段落。' : '暂无可展示片段，请先确认处理状态。'" :image-size="48" />
          </template><el-empty v-else-if="!sourceResource.loading.value && !sourceResource.error.value" description="选择文献后，在这里核对来源" :image-size="48" />
        </section>
        <section class="work-panel search-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="search" /></span><div><h2>检索片段</h2><p>从相关文献定位可以核对的内容</p></div><span v-if="searchResource.loaded.value" class="result-count">{{ hits.length }} 条结果</span></div><Feedback :busy="searchResource.loading.value" :error="searchResource.error.value" retry-label="重新检索" @retry="search" /><el-empty v-if="!searchResource.loading.value && !searchResource.error.value && !hits.length" :description="searchResource.loaded.value ? '当前范围内未找到相关内容' : '输入问题后开始检索'" :image-size="64" />
          <article v-for="hit in hits" :key="`${hit.source.chunk_id}:${hit.source.citation_id}`" class="source-hit"><div class="hit-heading"><span class="citation-number">{{ hit.source.citation_id }}</span><div><h3>{{ hit.paper.title }}</h3><div class="hit-metadata"><span>{{ hit.paper.year ?? '年份未标注' }}</span><span>第 {{ hit.source.page_number }} 页</span><span>{{ hit.source.synthetic ? '合成片段' : '原文片段，待核验' }}</span></div></div></div><blockquote>{{ hit.source.excerpt }}</blockquote><div class="hit-actions"><el-button text type="primary" @click="showSources([hit.source])">展开来源 [{{ hit.source.citation_id }}]<AppIcon name="arrow" :size="14" /></el-button><el-button text :disabled="busy || tasks.blocked.value" @click="viewEvidence(hit.paper.paper_id)">查看已有证据</el-button><el-button text :disabled="busy || tasks.blocked.value" @click="extract(hit.paper.paper_id)">{{ aiMock ? '提取 Mock 证据' : '提取证据' }}</el-button></div></article>
        </section>
      </div>
      <aside class="research-writing" aria-label="报告编辑与版本">
        <div class="assistant-tabs" role="tablist" aria-label="研究助手"><button v-for="tab in (['answer', 'evidence', 'report'] as const)" :key="tab" role="tab" :tabindex="assistantTab === tab ? 0 : -1" @keydown="moveAssistantTab($event, tab)" :aria-selected="assistantTab === tab" :aria-controls="`assistant-${tab}`" @click="assistantTab = tab">{{ { answer: '问答', evidence: '证据', report: '报告' }[tab] }}</button></div>
        <div id="assistant-answer" v-show="assistantTab === 'answer'" role="tabpanel" aria-label="问答"><el-empty v-if="!answer" description="提出研究问题后，生成有来源的回答" :image-size="48" />
        <section v-if="answer" v-show="assistantTab === 'answer'" class="work-panel answer-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="quote" /></span><div><h2>证据问答 <span class="provenance-badge">{{ answer.mock ? 'Mock' : '适配器结果，待核验' }}</span></h2><p>结合来源阅读，核验回答中的每个结论</p></div></div><el-alert v-if="answer.insufficient_evidence || !answer.sources.length" title="当前范围内证据不足" type="warning" :closable="false" /><div class="answer-prose"><p class="prewrap">{{ answer.answer }}</p></div><div v-if="answer.sources.length" class="answer-citations"><span>关联来源</span><el-button v-for="source in answer.sources" :key="source.citation_id" :aria-pressed="selectedPaperId === source.paper_id && activeChunkId === source.chunk_id" text type="primary" @click="showSources([source])">查看引用 [{{ source.citation_id }}]</el-button></div></section>
        </div>
        <div id="assistant-evidence" v-show="assistantTab === 'evidence'" role="tabpanel" aria-label="证据"><el-empty v-if="!evidence && !evidenceNotice" description="从检索结果读取已有证据或明确提取" :image-size="48" />
        <p v-if="evidenceNotice" v-show="assistantTab === 'evidence'" class="editor-notice" role="status">{{ evidenceNotice }}</p>
        <section v-if="evidence" v-show="assistantTab === 'evidence'" ref="evidencePanel" class="work-panel evidence-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="file" /></span><div><h2>结构化证据 · AI 提取，待核验{{ evidence.mock ? '（Mock）' : '' }}</h2><p>按字段展开，查看内容与支撑片段</p></div></div><p class="editor-notice">结果基于有限来源片段，可能未覆盖全文；请结合原文核验。</p><el-collapse v-model="expandedEvidence" class="evidence-fields"><el-collapse-item v-for="(label, field) in evidenceLabels" :key="field" :name="field"><template #title><strong>{{ label }}</strong><span class="field-status" :class="`field-status-${evidence[field].status}`">{{ evidenceStatusLabels[evidence[field].status] || evidence[field].status }}</span></template><p>{{ evidence[field].text || '未提及或证据不足' }}</p><el-button v-if="evidence[field].sources.length" text @click="showSources(evidence[field].sources)"><AppIcon name="quote" :size="14" />支撑来源</el-button></el-collapse-item></el-collapse></section>
        </div>
        <div id="assistant-report" v-show="assistantTab === 'report'" role="tabpanel" aria-label="报告">

        <section class="work-panel report-editor"><el-alert v-if="tasks.task.value?.kind === 'draft' && tasks.task.value.status === 'failed'" title="草稿生成失败，当前编辑和已保存版本未被覆盖。可查询原任务或明确重试。" type="warning" :closable="false" /><div class="work-panel-heading"><span class="work-icon"><AppIcon name="file" /></span><div><h2>综述草稿与版本</h2><p>把核验后的见解沉淀为研究成果</p></div></div><div v-if="incomingDraft" class="draft-arrival" role="status"><el-alert title="新草稿已完成；生成期间你修改了编辑内容，已保留当前编辑。" type="warning" :closable="false" /><div class="toolbar"><el-button @click="applyDraft(incomingDraft)">载入生成草稿</el-button><el-button @click="incomingDraft = null">保留当前编辑</el-button></div></div>
          <label class="field-label" for="report-title">报告标题</label><el-input id="report-title" v-model="reportTitle" placeholder="报告标题" aria-label="报告标题" />
          <div class="editor-label-row"><label class="field-label" for="report-body">报告正文</label><span v-if="editorDirty" class="unsaved-label">有未保存的编辑</span><span v-else-if="currentVersion" class="saved-label">当前版本 {{ currentVersion.number }}</span></div><el-input id="report-body" v-model="body" type="textarea" :rows="16" placeholder="生成草稿或在这里编辑报告" aria-label="报告正文" class="report-body-input" />
          <div class="editor-meta"><span>{{ body.length }} 字符</span><span>{{ reportSources.length }} 条引用快照</span></div><p v-if="body && !reportSources.length" class="editor-notice">当前正文没有来源快照，请自行核验，不能作为已核实证据。</p><p class="editor-notice" role="status">{{ notice || '保存版本时同时保存研究问题与引用快照。' }}</p>
          <div class="report-actions"><el-button type="primary" :disabled="!body.trim() || !reportTitle.trim() || !question.trim() || busy || tasks.busy.value" @click="save"><AppIcon name="save" :size="16" />保存新版本</el-button><el-button v-if="reportSources.length" @click="showSources(reportSources)"><AppIcon name="quote" :size="15" />引用清单</el-button></div><a v-if="currentVersion" class="report-export" :href="`/api/v1/projects/${pid}/reports/${currentVersion.report_id}/versions/${currentVersion.version_id}/text`"><AppIcon name="file" :size="14" />导出当前版本文本</a>
          <Feedback :busy="historyResource.loading.value" :error="historyResource.error.value" retry-label="重新加载版本" @retry="historyResource.load" /><div v-if="history.length" class="version-history"><h3><AppIcon name="clock" :size="15" />历史版本</h3><div class="version-chips"><el-button v-for="version in history" :key="version.version_id" size="small" :class="{ 'is-current': currentVersion?.version_id === version.version_id }" :disabled="busy || tasks.busy.value" @click="openVersion(version)">版本 {{ version.number }}</el-button></div></div><el-empty v-if="historyResource.loaded.value && !history.length" description="当前报告没有可读取版本" :image-size="48" />
        </section>
        <section class="work-panel saved-reports"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="folder" /></span><div><h2>已保存报告</h2><p>重新打开报告，继续已有研究</p></div></div><Feedback :busy="reportResource.loading.value" :error="reportResource.error.value" retry-label="重新加载报告" @retry="reportResource.load" /><el-empty v-if="reportResource.loaded.value && !reportResource.error.value && !reports.length" description="尚未保存报告" :image-size="48" /><el-button v-for="report in reports" :key="report.report_id" text class="saved-report-row" :disabled="busy || tasks.busy.value" @click="openReport(report)"><AppIcon name="file" :size="17" /><span>{{ report.title }} · v{{ report.number }}</span><AppIcon name="chevron" :size="14" /></el-button></section>
        </div>
      </aside>
    </div>
    <el-dialog v-model="leaveOpen" title="离开前处理未保存编辑" :close-on-click-modal="false" @close="finishLeave(false)"><p>{{ pendingSave ? '报告保存结果仍待确认，请先核对已有版本。' : '当前报告有未保存的编辑，切换后不会自动保留这些修改。' }}</p><template #footer><el-button @click="finishLeave(false)">留在当前项目</el-button><el-button type="primary" @click="finishLeave(true)">放弃未保存编辑并继续</el-button></template></el-dialog>
    <el-drawer v-model="sourceDrawer" :title="selectedSources.every(source => source.synthetic) ? '引用来源 · 合成数据' : '引用来源 · 待核验'" class="source-drawer" size="min(620px, 95vw)"><SourcesPanel :sources="selectedSources" /></el-drawer>
  </div>
</template>
