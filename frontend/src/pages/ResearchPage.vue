<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
const route = useRoute()
const pid = String(route.params.projectId), { busy, error, run } = useAction()
const question = ref('检索增强生成如何核验证据？'), mode = ref<Models['SearchRequest']['mode']>('hybrid')
const yearFrom = ref<number>(), yearTo = ref<number>()
const yearError = computed(() => yearProblem(yearFrom.value, yearTo.value))
const searchResource = useResource<Models['SearchHit'][]>([], async () => (await api.search(pid, { query: question.value, mode: mode.value, year_from: yearFrom.value, year_to: yearTo.value, limit: 10 })).items)
const hits = searchResource.data
const answer = ref<Models['AnswerResult'] | null>(null), evidence = ref<Models['EvidenceResult'] | null>(null)
const evidenceNotice = ref('')
const evidencePanel = ref<HTMLElement | null>(null)
const assistantTab = ref<'answer' | 'evidence' | 'report'>('answer')
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
  setAssistant('report'); leaveOpen.value = true
  if (!leavePromise) leavePromise = new Promise<boolean>(resolve => { resolveLeave = resolve })
  return leavePromise
}
onBeforeRouteLeave(guardEditor)
onBeforeRouteUpdate(to => String(to.params.projectId || '') !== pid ? guardEditor() : true)
const selectedSources = ref<Models['Source'][]>([]), sourceDrawer = ref(false)
const reportQuestion = ref(question.value)
const body = ref(''), reportTitle = ref('研究综述草稿'), reportSources = ref<Models['Source'][]>([]), currentVersion = ref<Models['ReportVersion'] | null>(null)
const editorDirty = computed(() => currentVersion.value
  ? body.value !== currentVersion.value.body || reportTitle.value !== currentVersion.value.title || reportQuestion.value !== currentVersion.value.research_question || JSON.stringify(reportSources.value) !== JSON.stringify(currentVersion.value.sources)
  : !!body.value.trim() || reportTitle.value !== '研究综述草稿' || reportQuestion.value !== '检索增强生成如何核验证据？' || !!reportSources.value.length)
const notice = ref(''), historyReportId = ref<string | null>(null)
const reportResource = useResource<Models['ReportVersion'][]>([], async () => (await api.reports(pid)).items)
const historyResource = useResource<Models['ReportVersion'][]>([], async () => historyReportId.value ? (await api.versions(pid, historyReportId.value)).items : [])
const reports = reportResource.data, history = historyResource.data
let pendingSave: { payload: string; key: string } | null = null
let restoring = true
let draftQuestion = ''
const incomingDraft = ref<Models['DraftResult'] | null>(null)
const editorSnapshot = () => JSON.stringify([body.value, reportTitle.value, reportQuestion.value, reportSources.value, currentVersion.value?.version_id])
let draftBaseline: string | null = editorSnapshot()
function applyDraft(result: Models['DraftResult']) {
  body.value = result.body; reportSources.value = result.sources; currentVersion.value = null; historyReportId.value = null; historyResource.reset()
  incomingDraft.value = null
  reportQuestion.value = restoring ? '' : draftQuestion
  notice.value = '草稿已载入，尚未保存。'
}
async function queryTask(id?: string) {
  // Explicitly opening another retained result is a user action, unlike a late worker response.
  if (id && id !== tasks.selected.value) draftBaseline = null
  if (id && id !== tasks.selected.value) submittedQuestion.value = ''
  await tasks.refresh(id)
}
const tasks = useTaskRecovery(pid, 'research', current => {
  const result = current.result
  if (result?.kind === 'answer') { answer.value = result; submittedScope.value = [...(tasks.record.value?.paperIds || [])]; if (!(editorDirty.value && assistantTab.value === 'report')) assistantTab.value = 'answer' }
  if (result?.kind === 'evidence') { evidence.value = result; evidenceNotice.value = ''; if (!(editorDirty.value && assistantTab.value === 'report')) assistantTab.value = 'evidence' }
  if (result?.kind === 'draft') {
    if ((draftBaseline !== null && editorSnapshot() !== draftBaseline) || (draftBaseline === null && editorDirty.value)) incomingDraft.value = result
    else applyDraft(result)
    draftBaseline = null
  }
})
onMounted(async () => {
  await Promise.all([reportResource.load(), library.load()])
  const retained = tasks.record.value
  scopeIds.value = retained && ['answer', 'draft'].includes(retained.kind) ? [...retained.paperIds] : library.data.value.filter(paper => paper.status === 'searchable').map(paper => paper.paper_id)
  const incoming = typeof route.query.paper === 'string' ? library.data.value.find(paper => paper.paper_id === route.query.paper) : null
  const first = incoming || library.data.value.find(paper => paper.status === 'searchable') || library.data.value[0]
  if (first) await selectPaper(first.paper_id)
})
async function search() { readerTab.value = 'search'; if (!yearError.value && !searchResource.loading.value) await searchResource.load() }
async function generate(kind: 'answer' | 'draft') {
  const prompt = kind === 'draft' ? reportQuestion.value : question.value
  if (!prompt.trim() || tasks.blocked.value || !scopeIds.value.length || scopeIds.value.length > 50) return
  restoring = false
  if (kind === 'draft') { draftQuestion = prompt; draftBaseline = editorSnapshot(); incomingDraft.value = null; assistantTab.value = 'report' }
  else { submittedQuestion.value = prompt; submittedScope.value = [...scopeIds.value] }
  await tasks.submit({ kind, question: prompt, paperIds: scopeIds.value })
}
async function extract(paperId: string) {
  restoring = false; evidencePaper.value = paperId; setAssistant('evidence')
  await tasks.submit({ kind: 'evidence', paperId })
}
async function viewEvidence(paperId: string) {
  if (tasks.blocked.value) return
  await run(async () => {
    evidencePaper.value = paperId
    const result = await api.evidenceHistory(pid, paperId)
    evidence.value = result.items[result.items.length - 1] || null
    setAssistant('evidence')
    evidenceNotice.value = evidence.value ? '已读取该文献最近保存的证据，未重新提取。' : '当前文献尚未保存结构化证据，可使用“提取证据”创建任务。'

  })
}
async function reconcile() {
  const item = tasks.record.value
  if (!item) return
  restoring = false
  await tasks.submit({ kind: item.kind, question: item.kind === 'draft' ? reportQuestion.value : question.value, paperId: item.paperId }, true)
}
function showSources(sources: Models['Source'][]) { selectedSources.value = sources; sourceDrawer.value = true }
function addSourceToReport() {
  const source = activeSource.value
  if (!source) return
  assistantTab.value = 'report'; mobileView.value = 'assistant'
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
    const payload = { title: reportTitle.value, research_question: reportQuestion.value, body: body.value, sources: reportSources.value, report_id: currentVersion.value?.report_id, base_version_id: currentVersion.value?.version_id }
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
async function openReport(report: Models['ReportVersion']) { if (!await guardEditor()) return; await run(async () => { historyReportId.value = report.report_id; if (await historyResource.load()) { const latest = history.value[history.value.length - 1]; if (latest) loadVersion(latest) } }) }
function loadVersion(version: Models['ReportVersion']) { assistantTab.value = 'report'; currentVersion.value = version; body.value = version.body; reportTitle.value = version.title; reportQuestion.value = version.research_question; reportSources.value = version.sources; notice.value = `当前查看版本 ${version.number}` }
async function openVersion(version: Models['ReportVersion']) { if (await guardEditor()) loadVersion(version) }
async function acceptDraft() { if (incomingDraft.value && await guardEditor()) applyDraft(incomingDraft.value) }
const railQuery = ref(''), railStatus = ref(''), railYear = ref<number>()
const filteredLibrary = computed(() => library.data.value.filter(paper => paper.title.toLowerCase().includes(railQuery.value.toLowerCase()) && (!railStatus.value || paper.status === railStatus.value) && (!railYear.value || paper.year === railYear.value)))
const years = computed(() => [...new Set(library.data.value.map(paper => paper.year).filter((year): year is number => year !== null))].sort((a,b) => b-a))
const scopeIds = ref<string[]>([]), scopeOpen = ref(false), sourcesOpen = ref(true)
const submittedQuestion = ref(''), submittedScope = ref<string[]>([])
const readerTab = ref<'source' | 'search'>('source'), mobileView = ref<'read' | 'assistant'>('read')
function moveReaderTab(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  readerTab.value = event.key === 'Home' ? 'source' : event.key === 'End' ? 'search' : readerTab.value === 'source' ? 'search' : 'source'
  document.getElementById(`reader-${readerTab.value}-tab`)?.focus()
}
const sourceIndex = computed(() => sourceResource.data.value?.sources.findIndex(source => source.chunk_id === activeChunkId.value) ?? -1)
const previousSource = ref<Models['Source'] | null>(null)
const readerScroll = ref<HTMLElement | null>(null)
let previousScroll = 0
let sourceTrigger: HTMLElement | null = null
async function locateSource(source: Models['Source'], event?: Event) {
  if (activeSource.value) { previousSource.value = { ...activeSource.value }; previousScroll = readerScroll.value?.scrollTop || 0 }
  if (event?.currentTarget instanceof HTMLElement) sourceTrigger = event.currentTarget
  readerTab.value = 'source'; mobileView.value = 'read'; sourceDrawer.value = false
  await selectPaper(source.paper_id, source.chunk_id, source.citation_id)
  await nextTick(); readerScroll.value?.scrollTo({ top: 0 })
}
async function returnToSource() {
  const source = previousSource.value
  if (!source) return
  await selectPaper(source.paper_id, source.chunk_id, source.citation_id)
  await nextTick(); readerScroll.value?.scrollTo({ top: previousScroll }); previousSource.value = null
  sourceTrigger?.focus({ preventScroll: true })
}
function stepSource(offset: number) {
  const source = sourceResource.data.value?.sources[sourceIndex.value + offset]
  if (source) chooseSource(source)
}
function setAssistant(tab: 'answer' | 'evidence' | 'report') { assistantTab.value = tab; mobileView.value = 'assistant' }
function paperName(id: string) { return library.data.value.find(paper => paper.paper_id === id)?.title || '引用文献' }
const selectedPaper = computed(() => library.data.value.find(paper => paper.paper_id === selectedPaperId.value))
const statusLabels = { searchable: '可检索', pending: '待处理', processing: '处理中', failed: '处理失败' }
const evidencePaper = ref('')
watch(() => route.query.view, value => { if (value === 'report') setAssistant('report') }, { immediate: true })
watch(() => route.query.paper, value => {
  if (typeof value === 'string' && library.data.value.some(paper => paper.paper_id === value)) void selectPaper(value)
})
function preventUnload(event: BeforeUnloadEvent) { if (editorDirty.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => window.addEventListener('beforeunload', preventUnload))
onUnmounted(() => window.removeEventListener('beforeunload', preventUnload))
const expandedEvidence = ref<string[]>(['research_question', 'method'])
const evidenceStatusLabels: Record<string, string> = { pending_verification: '待人工核验', not_mentioned: '文献未提及', insufficient_evidence: '证据不足' }
const evidenceLabels = { research_question: '研究问题', method: '研究方法', results: '实验结果', conclusion: '主要结论', limitations: '研究局限' } as const
</script>
<template>
  <div class="research-desk" :data-mobile-view="mobileView">
    <div class="work-item-tabs">
      <button :class="{ active: mobileView === 'read' }" @click="readerTab = 'source'; mobileView = 'read'"><AppIcon name="file" :size="16" /><span>{{ selectedPaper?.title || '来源阅读' }}</span></button>
      <button :class="{ active: mobileView === 'assistant' && assistantTab === 'report' }" @click="setAssistant('report')"><AppIcon name="save" :size="16" /><span>{{ reportTitle }}</span><i v-if="editorDirty" class="unsaved-dot" aria-label="有未保存的编辑" /></button>
      <RouterLink :to="`/projects/${pid}/papers?import=1`" class="desk-icon-button" aria-label="导入文献" title="导入文献"><AppIcon name="plus" :size="18" /></RouterLink>
    </div>
    <Feedback :error="error" />
    <TaskRecoveryPanel compact :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="queryTask" @reconcile="reconcile" @retry="tasks.retryFailed" />
    <nav class="desk-mobile-tabs" aria-label="研究视图"><button :aria-pressed="mobileView === 'read'" @click="mobileView = 'read'">阅读</button><button v-for="tab in (['answer', 'evidence', 'report'] as const)" :key="tab" :aria-pressed="mobileView === 'assistant' && assistantTab === tab" @click="setAssistant(tab)">{{ { answer: '问答', evidence: '证据', report: '报告' }[tab] }}</button></nav>
    <div class="desk-panes">
      <section class="desk-library" aria-label="研究文献列表">
        <div class="pane-heading"><h2>项目文献 <span class="count-badge">{{ library.data.value.length }}</span></h2><RouterLink :to="`/projects/${pid}/papers?import=1`" class="desk-icon-button" aria-label="添加项目文献" title="导入文献"><AppIcon name="plus" :size="18" /></RouterLink></div>
        <div class="rail-filters"><el-input v-model="railQuery" placeholder="搜索标题…" aria-label="搜索研究文献" clearable><template #prefix><AppIcon name="search" :size="16" /></template></el-input><div class="rail-filter-row"><el-select v-model="railStatus" placeholder="全部状态" aria-label="文献状态" clearable><el-option v-for="(label, value) in statusLabels" :key="value" :value="value" :label="label" /></el-select><el-select v-model="railYear" placeholder="年份" aria-label="文献年份" clearable><el-option v-for="year in years" :key="year" :value="year" :label="String(year)" /></el-select></div></div>
        <Feedback :busy="library.loading.value" :error="library.error.value" retry-label="重新读取项目文献" @retry="library.load" />
        <div class="desk-paper-list"><button v-for="paper in filteredLibrary" :key="paper.paper_id" class="desk-paper" :class="{ selected: selectedPaperId === paper.paper_id }" :aria-pressed="selectedPaperId === paper.paper_id" :aria-label="`选择文献 ${paper.title}`" @click="selectPaper(paper.paper_id); readerTab = 'source'"><AppIcon name="file" :size="20" /><span><strong>{{ paper.title }}</strong><span class="paper-metadata">{{ paper.year || '未标注年份' }}<span>·</span><span :class="`paper-state-${paper.status}`">{{ statusLabels[paper.status] }}</span></span></span></button></div>
        <div v-if="library.loaded.value && !filteredLibrary.length" class="desk-empty"><p>{{ library.data.value.length ? '没有匹配文献' : '项目尚无文献' }}</p><el-button v-if="library.data.value.length" text @click="railQuery = ''; railYear = undefined; railStatus = ''">清空筛选</el-button><RouterLink v-else :to="`/projects/${pid}/papers?import=1`">导入文献</RouterLink></div>
      </section>
      <section class="desk-reader" aria-label="当前来源阅读区">
        <div class="reader-heading"><div><h2>{{ selectedPaper?.title || '来源阅读' }}</h2><span v-if="selectedPaper" class="reader-year">{{ selectedPaper.year || '年份未标注' }}</span></div><el-select class="desk-paper-picker" :model-value="selectedPaperId" aria-label="选择研究文献" @change="selectPaper"><el-option v-for="paper in library.data.value" :key="paper.paper_id" :value="paper.paper_id" :label="paper.title" /></el-select></div>
        <div class="reader-view-tabs" role="tablist" aria-label="阅读内容"><button id="reader-source-tab" role="tab" :tabindex="readerTab === 'source' ? 0 : -1" @keydown="moveReaderTab" :aria-selected="readerTab === 'source'" aria-controls="reader-source" @click="readerTab = 'source'">来源片段</button><button id="reader-search-tab" role="tab" :tabindex="readerTab === 'search' ? 0 : -1" @keydown="moveReaderTab" :aria-selected="readerTab === 'search'" aria-controls="reader-search" @click="readerTab = 'search'">检索结果</button></div>
        <div ref="readerScroll" class="reader-scroll">
          <div id="reader-source" v-show="readerTab === 'source'" role="tabpanel" aria-labelledby="reader-source-tab">
            <Feedback :busy="sourceResource.loading.value" :error="sourceResource.error.value" retry-label="重新读取来源" @retry="selectPaper(selectedPaperId, requestedChunkId, activeCitation)" />
            <template v-if="activeSource"><div class="reader-pagination"><span>第 {{ activeSource.page_number }} 页 · 片段 {{ sourceIndex + 1 }} / {{ sourceResource.data.value?.sources.length }}</span><div><button class="desk-icon-button" :disabled="sourceIndex <= 0" aria-label="上一片段" @click="stepSource(-1)"><AppIcon class="rotate-icon" name="chevron" :size="16" /></button><button class="desk-icon-button" :disabled="sourceIndex >= (sourceResource.data.value?.sources.length || 0) - 1" aria-label="下一片段" @click="stepSource(1)"><AppIcon name="chevron" :size="16" /></button></div><button v-if="previousSource" class="return-source" @click="returnToSource">返回上次位置</button></div>
              <article class="reading-document"><blockquote class="desk-excerpt" :data-active-chunk="activeSource.chunk_id" data-testid="active-source"><mark>{{ activeSource.excerpt }}</mark><span class="desk-citation">[{{ activeCitation || activeSource.citation_id }}]</span></blockquote><span class="reading-provenance">{{ activeSource.synthetic ? '合成片段' : '来源片段' }} · 待核验</span></article>
            </template>
            <div v-else-if="sourceResource.data.value && !sourceResource.loading.value && !sourceResource.error.value" class="desk-empty"><AppIcon name="file" :size="32" /><p>{{ sourceResource.data.value.paper.status === 'failed' ? '文献处理失败，暂无来源。' : requestedChunkId ? '引用片段已不在当前文献中' : '暂无来源片段' }}</p><el-button v-if="selectedSources.length" text @click="sourceDrawer = true">查看引用快照</el-button></div>
            <div v-else-if="!sourceResource.loading.value && !sourceResource.error.value" class="desk-empty"><AppIcon name="book" :size="34" /><p>选择文献，开始阅读</p></div>
          </div>
          <div id="reader-search" v-show="readerTab === 'search'" role="tabpanel" aria-labelledby="reader-search-tab" class="desk-search">
            <form @submit.prevent="search"><el-input v-model="question" aria-label="检索问题" placeholder="查找项目中的相关片段" /><div class="search-filter-row"><el-select v-model="mode" aria-label="检索模式"><el-option value="keyword" label="关键词" /><el-option value="semantic" label="语义" /><el-option value="hybrid" label="组合" /></el-select><el-input-number v-model="yearFrom" aria-label="检索起始年份" placeholder="起始年份" :controls="false" :min="1000" :max="3000" /><span>—</span><el-input-number v-model="yearTo" aria-label="检索结束年份" placeholder="结束年份" :controls="false" :min="1000" :max="3000" /></div><div class="search-submit-row"><span>在项目文献中检索</span><el-button native-type="submit" type="primary" :disabled="!question.trim() || !!yearError || searchResource.loading.value">检索</el-button></div></form>
            <p v-if="yearError" class="field-error" role="alert">{{ yearError }}</p><Feedback :busy="searchResource.loading.value" :error="searchResource.error.value" retry-label="重新检索" @retry="search" />
            <div v-if="!searchResource.loading.value && !searchResource.error.value && !hits.length" class="desk-empty"><p>{{ searchResource.loaded.value ? '当前范围内未找到相关内容' : '输入问题，查找来源' }}</p></div>
            <article v-for="hit in hits" :key="`${hit.source.paper_id}:${hit.source.chunk_id}:${hit.source.citation_id}`" class="source-hit"><h3>{{ hit.paper.title }}</h3><span class="hit-page">第 {{ hit.source.page_number }} 页</span><blockquote>{{ hit.source.excerpt }}</blockquote><div class="hit-actions"><el-button text type="primary" @click="selectedSources = [hit.source]; locateSource(hit.source, $event)">展开来源 [{{ hit.source.citation_id }}]</el-button><el-button text :disabled="busy || tasks.blocked.value" @click="viewEvidence(hit.paper.paper_id)">查看已有证据</el-button><el-button :aria-label="aiMock ? '提取 Mock 证据' : '提取证据'" text :disabled="busy || tasks.blocked.value" @click="extract(hit.paper.paper_id)">提取证据</el-button></div></article>
          </div>
        </div>
        <div class="desk-reader-footer"><span>{{ activeSource ? `第 ${activeSource.page_number} 页` : '来源片段' }}</span><el-button v-if="activeSource" type="primary" aria-label="添加来源到报告" @click="addSourceToReport"><AppIcon name="plus" :size="16" />加入报告</el-button></div>
      </section>
      <aside class="desk-assistant" aria-label="报告编辑与版本">
        <div class="pane-heading"><h2>研究助手</h2><AppIcon name="quote" :size="18" /></div>
        <div class="assistant-tabs" role="tablist" aria-label="研究助手"><button v-for="tab in (['answer', 'evidence', 'report'] as const)" :id="`tab-${tab}`" :key="tab" role="tab" :tabindex="assistantTab === tab ? 0 : -1" @keydown="moveAssistantTab($event, tab)" :aria-selected="assistantTab === tab" :aria-controls="`assistant-${tab}`" @click="setAssistant(tab)">{{ { answer: '问答', evidence: '证据', report: '报告' }[tab] }}<i v-if="tab === 'report' && editorDirty" class="unsaved-dot" /></button></div>
        <div id="assistant-answer" v-show="assistantTab === 'answer'" role="tabpanel" aria-labelledby="tab-answer" class="answer-workspace">
          <button class="scope-button" title="生成使用所选文献；片段检索按项目与检索条件进行" @click="scopeOpen = true"><AppIcon name="file" :size="17" /><span>生成材料：已选 {{ scopeIds.length }} 篇</span><AppIcon name="chevron" :size="15" /></button>
          <div class="assistant-scroll"><template v-if="answer"><p v-if="submittedQuestion" class="question-bubble">{{ submittedQuestion }}</p><h3 class="answer-heading"><AppIcon name="quote" :size="18" />基于所选文献的回答</h3><el-alert v-if="answer.insufficient_evidence || !answer.sources.length" title="当前范围内证据不足" type="warning" :closable="false" /><p class="answer-text prewrap">{{ answer.answer }}</p><p class="answer-provenance">{{ answer.mock ? '示例回答' : 'AI 回答' }} · 待核验</p><button class="source-list-toggle" :aria-expanded="sourcesOpen" @click="sourcesOpen = !sourcesOpen">关联来源 <span class="count-badge">{{ answer.sources.length }}</span><AppIcon name="chevron" :size="14" /></button><div v-if="sourcesOpen" class="answer-source-list"><button v-for="source in answer.sources" :key="`${source.paper_id}:${source.chunk_id}:${source.citation_id}`" :aria-label="`查看引用 [${source.citation_id}]`" :aria-pressed="selectedPaperId === source.paper_id && activeChunkId === source.chunk_id" @click="selectedSources = [source]; locateSource(source, $event)"><AppIcon name="file" :size="16" /><span>[{{ source.citation_id }}] {{ paperName(source.paper_id) }}</span><span class="source-page">第 {{ source.page_number }} 页</span></button></div><el-button v-if="answer.sources.length" text @click="showSources(answer.sources)">查看全部来源</el-button></template><div v-else class="desk-empty assistant-empty"><AppIcon name="quote" :size="30" /><h3>带着问题阅读</h3><p>选择文献，生成有来源的回答</p></div></div>
          <form class="desk-composer" @submit.prevent="generate('answer')"><el-input id="research-question" v-model="question" type="textarea" :rows="3" maxlength="2000" aria-label="研究问题" placeholder="提出研究问题…" /><div class="composer-actions"><button type="button" class="composer-scope" @click="scopeOpen = true">{{ scopeIds.length }} 篇材料</button><el-button :aria-label="aiMock ? '生成 Mock 回答' : '生成回答'" type="primary" native-type="submit" :disabled="!question.trim() || !scopeIds.length || scopeIds.length > 50 || busy || tasks.blocked.value" :loading="tasks.busy.value && tasks.task.value?.kind === 'answer'">生成回答<AppIcon name="arrow" :size="16" /></el-button></div></form>
        </div>
        <div id="assistant-evidence" v-show="assistantTab === 'evidence'" role="tabpanel" aria-labelledby="tab-evidence" class="assistant-scroll evidence-workspace"><h3>{{ evidencePaper ? paperName(evidencePaper) : selectedPaper?.title || '文献证据' }}</h3><div class="evidence-actions"><el-button :disabled="!selectedPaperId || busy || tasks.blocked.value" @click="viewEvidence(selectedPaperId)">查看已有证据</el-button><el-button :aria-label="aiMock ? '提取 Mock 证据' : '提取证据'" type="primary" :disabled="!selectedPaperId || busy || tasks.blocked.value" @click="extract(selectedPaperId)">提取证据</el-button></div><p v-if="evidenceNotice" role="status">{{ evidenceNotice }}</p><div v-if="evidence" ref="evidencePanel"><p class="answer-provenance">{{ evidence.mock ? 'Mock 证据' : 'AI 提取' }} · 待核验</p><el-collapse v-model="expandedEvidence" class="evidence-fields"><el-collapse-item v-for="(label, field) in evidenceLabels" :key="field" :name="field"><template #title><strong>{{ label }}</strong><span class="field-status">{{ evidenceStatusLabels[evidence[field].status] || evidence[field].status }}</span></template><p>{{ evidence[field].text || '未提及或证据不足' }}</p><el-button v-for="source in evidence[field].sources" :key="source.chunk_id" text type="primary" @click="selectedSources = [source]; locateSource(source, $event)">支撑来源 [{{ source.citation_id }}]</el-button></el-collapse-item></el-collapse></div><div v-else-if="!evidenceNotice" class="desk-empty"><p>查看这篇文献的研究问题、方法与结论</p></div></div>
        <div id="assistant-report" v-show="assistantTab === 'report'" role="tabpanel" aria-labelledby="tab-report" class="assistant-scroll report-workspace"><el-alert v-if="tasks.task.value?.kind === 'draft' && tasks.task.value.status === 'failed'" title="草稿生成失败，当前编辑和已保存版本未被覆盖。可查询原任务或明确重试。" type="warning" :closable="false" /><div class="draft-generate-row"><button class="composer-scope" @click="scopeOpen = true">{{ scopeIds.length }} 篇材料</button><el-button :aria-label="aiMock ? '生成 Mock 草稿' : '生成草稿'" :disabled="!reportQuestion.trim() || !scopeIds.length || scopeIds.length > 50 || busy || tasks.blocked.value" @click="generate('draft')">生成草稿</el-button></div><div v-if="incomingDraft" class="draft-arrival" role="status"><p>新草稿已完成，当前编辑已保留。</p><el-button @click="acceptDraft">载入生成草稿</el-button><el-button @click="incomingDraft = null">保留当前编辑</el-button></div>
          <label class="field-label" for="report-title">报告标题</label><el-input id="report-title" v-model="reportTitle" aria-label="报告标题" maxlength="200" /><label class="field-label" for="report-question">研究问题</label><el-input id="report-question" v-model="reportQuestion" aria-label="报告研究问题" maxlength="2000" placeholder="这份报告要回答什么？" /><div class="editor-label-row"><label class="field-label" for="report-body">报告正文</label><span v-if="editorDirty" class="unsaved-label">未保存</span><span v-else-if="currentVersion" class="saved-label">版本 {{ currentVersion.number }}</span></div><el-input id="report-body" v-model="body" type="textarea" :rows="14" maxlength="100000" aria-label="报告正文" placeholder="写下研究结论，或从来源片段加入引用…" class="report-body-input" /><p v-if="notice" class="editor-notice" role="status">{{ notice }}</p><div class="report-actions"><el-button type="primary" :disabled="!body.trim() || !reportTitle.trim() || !reportQuestion.trim() || busy || tasks.busy.value" @click="save"><AppIcon name="save" :size="16" />保存新版本</el-button><el-button v-if="reportSources.length" @click="showSources(reportSources)">引用清单 {{ reportSources.length }}</el-button></div><a v-if="currentVersion" class="report-export" :href="`/api/v1/projects/${pid}/reports/${currentVersion.report_id}/versions/${currentVersion.version_id}/text`">导出当前版本文本</a><Feedback :busy="historyResource.loading.value" :error="historyResource.error.value" retry-label="重新加载版本" @retry="historyResource.load" /><div v-if="history.length" class="version-history"><h3>历史版本</h3><div class="version-chips"><el-button v-for="version in history" :key="version.version_id" size="small" :class="{ 'is-current': currentVersion?.version_id === version.version_id }" :disabled="busy || tasks.busy.value" @click="openVersion(version)">版本 {{ version.number }}</el-button></div></div>
          <section class="saved-reports"><h3>已保存报告</h3><Feedback :busy="reportResource.loading.value" :error="reportResource.error.value" retry-label="重新加载报告" @retry="reportResource.load" /><p v-if="reportResource.loaded.value && !reportResource.error.value && !reports.length" class="desk-empty">尚未保存报告</p><el-button v-for="report in reports" :key="report.report_id" text class="saved-report-row" :disabled="busy || tasks.busy.value" @click="openReport(report)"><AppIcon name="file" :size="16" /><span>{{ report.title }} · v{{ report.number }}</span><AppIcon name="chevron" :size="14" /></el-button></section>
        </div>
      </aside>
    </div>
    <el-dialog v-model="scopeOpen" title="选择生成材料" width="min(560px, 94vw)"><p>仅选择可检索文献，最多 50 篇。</p><Feedback :error="library.error.value" retry-label="重新读取项目文献" @retry="library.load" /><div class="scope-tools"><el-button text @click="scopeIds = library.data.value.filter(paper => paper.status === 'searchable').slice(0, 50).map(paper => paper.paper_id)">选择前 50 篇</el-button><el-button text @click="scopeIds = []">清空</el-button><strong>已选 {{ scopeIds.length }} 篇</strong></div><el-checkbox-group v-model="scopeIds" class="scope-options"><el-checkbox v-for="paper in library.data.value" :key="paper.paper_id" :value="paper.paper_id" :disabled="paper.status !== 'searchable'"><span>{{ paper.title }}</span><span v-if="paper.status !== 'searchable'"> · {{ statusLabels[paper.status] }}</span></el-checkbox></el-checkbox-group><p v-if="scopeIds.length > 50" class="field-error" role="alert">最多选择 50 篇，请减少材料。</p><template #footer><el-button type="primary" :disabled="scopeIds.length > 50" @click="scopeOpen = false">完成</el-button></template></el-dialog>
    <el-dialog v-model="leaveOpen" title="离开前处理未保存编辑" :close-on-click-modal="false" @close="finishLeave(false)"><p>{{ pendingSave ? '保存结果待确认，请先核对版本。' : '当前报告有未保存的编辑。' }}</p><template #footer><el-button @click="finishLeave(false)">留在当前项目</el-button><el-button type="primary" @click="finishLeave(true)">放弃未保存编辑并继续</el-button></template></el-dialog>
    <el-drawer v-model="sourceDrawer" :title="selectedSources.every(source => source.synthetic) ? '引用来源 · 合成数据' : '引用来源 · 待核验'" class="source-drawer" size="min(620px, 95vw)"><SourcesPanel :sources="selectedSources" locatable @locate="locateSource" /></el-drawer>
  </div>
</template>
