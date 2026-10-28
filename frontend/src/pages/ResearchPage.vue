<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
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
const selectedSources = ref<Models['Source'][]>([]), sourceDrawer = ref(false)
const body = ref(''), reportTitle = ref('研究综述草稿'), reportSources = ref<Models['Source'][]>([]), currentVersion = ref<Models['ReportVersion'] | null>(null)
const editorDirty = computed(() => currentVersion.value
  ? body.value !== currentVersion.value.body || reportTitle.value !== currentVersion.value.title || question.value !== currentVersion.value.research_question
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
  if (result?.kind === 'answer') answer.value = result
  if (result?.kind === 'evidence') { evidence.value = result; evidenceNotice.value = '' }
  if (result?.kind === 'draft') {
    if (draftBaseline !== null && editorSnapshot() !== draftBaseline) incomingDraft.value = result
    else applyDraft(result)
    draftBaseline = null
  }
})
onMounted(reportResource.load)
async function search() { if (!yearError.value && !searchResource.loading.value) await searchResource.load() }
async function generate(kind: 'answer' | 'draft') {
  if (tasks.blocked.value) return
  restoring = false
  if (kind === 'draft') { draftBaseline = editorSnapshot(); incomingDraft.value = null }
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
function showSources(sources: Models['Source'][]) { selectedSources.value = sources; sourceDrawer.value = true }
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
function openVersion(version: Models['ReportVersion']) { currentVersion.value = version; body.value = version.body; reportTitle.value = version.title; question.value = version.research_question; reportSources.value = version.sources; notice.value = `当前查看版本 ${version.number}` }
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
    <div class="research-columns">
      <div class="research-reading">
        <section v-if="answer" class="work-panel answer-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="quote" /></span><div><h2>证据问答 <span class="provenance-badge">{{ answer.mock ? 'Mock' : '适配器结果，待核验' }}</span></h2><p>结合来源阅读，核验回答中的每个结论</p></div></div><el-alert v-if="answer.insufficient_evidence || !answer.sources.length" title="当前范围内证据不足" type="warning" :closable="false" /><div class="answer-prose"><p class="prewrap">{{ answer.answer }}</p></div><div v-if="answer.sources.length" class="answer-citations"><span>关联来源</span><el-button v-for="source in answer.sources" :key="source.citation_id" text type="primary" @click="showSources([source])">查看引用 [{{ source.citation_id }}]</el-button></div></section>
        <section class="work-panel search-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="search" /></span><div><h2>检索片段</h2><p>从相关文献定位可以核对的内容</p></div><span v-if="searchResource.loaded.value" class="result-count">{{ hits.length }} 条结果</span></div><Feedback :busy="searchResource.loading.value" :error="searchResource.error.value" retry-label="重新检索" @retry="search" /><el-empty v-if="!searchResource.loading.value && !searchResource.error.value && !hits.length" :description="searchResource.loaded.value ? '当前范围内未找到相关内容' : '输入问题后开始检索'" :image-size="64" />
          <article v-for="hit in hits" :key="`${hit.source.chunk_id}:${hit.source.citation_id}`" class="source-hit"><div class="hit-heading"><span class="citation-number">{{ hit.source.citation_id }}</span><div><h3>{{ hit.paper.title }}</h3><div class="hit-metadata"><span>{{ hit.paper.year ?? '年份未标注' }}</span><span>第 {{ hit.source.page_number }} 页</span><span>{{ hit.source.synthetic ? '合成片段' : '原文片段，待核验' }}</span></div></div></div><blockquote>{{ hit.source.excerpt }}</blockquote><div class="hit-actions"><el-button text type="primary" @click="showSources([hit.source])">展开来源 [{{ hit.source.citation_id }}]<AppIcon name="arrow" :size="14" /></el-button><el-button text :disabled="busy || tasks.blocked.value" @click="viewEvidence(hit.paper.paper_id)">查看已有证据</el-button><el-button text :disabled="busy || tasks.blocked.value" @click="extract(hit.paper.paper_id)">{{ aiMock ? '提取 Mock 证据' : '提取证据' }}</el-button></div></article>
        </section>
        <p v-if="evidenceNotice" class="editor-notice" role="status">{{ evidenceNotice }}</p>
        <section v-if="evidence" ref="evidencePanel" class="work-panel evidence-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="file" /></span><div><h2>结构化证据 · AI 提取，待核验{{ evidence.mock ? '（Mock）' : '' }}</h2><p>按字段展开，查看内容与支撑片段</p></div></div><el-collapse v-model="expandedEvidence" class="evidence-fields"><el-collapse-item v-for="(label, field) in evidenceLabels" :key="field" :name="field"><template #title><strong>{{ label }}</strong><span class="field-status" :class="`field-status-${evidence[field].status}`">{{ evidenceStatusLabels[evidence[field].status] || evidence[field].status }}</span></template><p>{{ evidence[field].text || '未提及或证据不足' }}</p><el-button v-if="evidence[field].sources.length" text @click="showSources(evidence[field].sources)"><AppIcon name="quote" :size="14" />支撑来源</el-button></el-collapse-item></el-collapse></section>
      </div>
      <aside class="research-writing" aria-label="报告编辑与版本">
        <section class="work-panel report-editor"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="file" /></span><div><h2>综述草稿与版本</h2><p>把核验后的见解沉淀为研究成果</p></div></div><div v-if="incomingDraft" class="draft-arrival" role="status"><el-alert title="新草稿已完成；生成期间你修改了编辑内容，已保留当前编辑。" type="warning" :closable="false" /><div class="toolbar"><el-button @click="applyDraft(incomingDraft)">载入生成草稿</el-button><el-button @click="incomingDraft = null">保留当前编辑</el-button></div></div>
          <label class="field-label" for="report-title">报告标题</label><el-input id="report-title" v-model="reportTitle" placeholder="报告标题" aria-label="报告标题" />
          <div class="editor-label-row"><label class="field-label" for="report-body">报告正文</label><span v-if="editorDirty" class="unsaved-label">有未保存的编辑</span><span v-else-if="currentVersion" class="saved-label">当前版本 {{ currentVersion.number }}</span></div><el-input id="report-body" v-model="body" type="textarea" :rows="16" placeholder="生成草稿或在这里编辑报告" aria-label="报告正文" class="report-body-input" />
          <div class="editor-meta"><span>{{ body.length }} 字符</span><span>{{ reportSources.length }} 条引用快照</span></div><p v-if="body && !reportSources.length" class="editor-notice">当前正文没有来源快照，请自行核验，不能作为已核实证据。</p><p class="editor-notice" role="status">{{ notice || '保存版本时同时保存研究问题与引用快照。' }}</p>
          <div class="report-actions"><el-button type="primary" :disabled="!body.trim() || !reportTitle.trim() || !question.trim() || busy || tasks.busy.value" @click="save"><AppIcon name="save" :size="16" />保存新版本</el-button><el-button v-if="reportSources.length" @click="showSources(reportSources)"><AppIcon name="quote" :size="15" />引用清单</el-button></div><a v-if="currentVersion" class="report-export" :href="`/api/v1/projects/${pid}/reports/${currentVersion.report_id}/versions/${currentVersion.version_id}/text`"><AppIcon name="file" :size="14" />导出当前版本文本</a>
          <Feedback :busy="historyResource.loading.value" :error="historyResource.error.value" retry-label="重新加载版本" @retry="historyResource.load" /><div v-if="history.length" class="version-history"><h3><AppIcon name="clock" :size="15" />历史版本</h3><div class="version-chips"><el-button v-for="version in history" :key="version.version_id" size="small" :class="{ 'is-current': currentVersion?.version_id === version.version_id }" :disabled="busy || tasks.busy.value" @click="openVersion(version)">版本 {{ version.number }}</el-button></div></div><el-empty v-if="historyResource.loaded.value && !history.length" description="当前报告没有可读取版本" :image-size="48" />
        </section>
        <section class="work-panel saved-reports"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="folder" /></span><div><h2>已保存报告</h2><p>重新打开报告，继续已有研究</p></div></div><Feedback :busy="reportResource.loading.value" :error="reportResource.error.value" retry-label="重新加载报告" @retry="reportResource.load" /><el-empty v-if="reportResource.loaded.value && !reportResource.error.value && !reports.length" description="尚未保存报告" :image-size="48" /><el-button v-for="report in reports" :key="report.report_id" text class="saved-report-row" :disabled="busy || tasks.busy.value" @click="openReport(report)"><AppIcon name="file" :size="17" /><span>{{ report.title }} · v{{ report.number }}</span><AppIcon name="chevron" :size="14" /></el-button></section>
      </aside>
    </div>
    <el-drawer v-model="sourceDrawer" :title="selectedSources.every(source => source.synthetic) ? '引用来源 · 合成数据' : '引用来源 · 待核验'" class="source-drawer" size="min(620px, 95vw)"><SourcesPanel :sources="selectedSources" /></el-drawer>
  </div>
</template>
