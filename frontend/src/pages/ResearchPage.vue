<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Models } from '../api/client'
import { useTaskRecovery } from '../composables/useTaskRecovery'
import TaskRecoveryPanel from '../components/TaskRecoveryPanel.vue'
import { useAction } from '../composables/useAction'
import Feedback from '../components/Feedback.vue'
import SourcesPanel from '../components/SourcesPanel.vue'
import { persistentData } from '../state'
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
const question = ref('检索增强生成如何核验证据？'), mode = ref<Models['SearchRequest']['mode']>('hybrid')
const yearFrom = ref<number>(), yearTo = ref<number>(), hits = ref<Models['SearchHit'][]>([]), searched = ref(false)
const answer = ref<Models['AnswerResult'] | null>(null), evidence = ref<Models['EvidenceResult'] | null>(null)
const selectedSources = ref<Models['Source'][]>([]), sourceDrawer = ref(false)
const body = ref(''), reportTitle = ref('研究综述草稿'), reportSources = ref<Models['Source'][]>([]), currentVersion = ref<Models['ReportVersion'] | null>(null)
const reports = ref<Models['ReportVersion'][]>([]), history = ref<Models['ReportVersion'][]>([]), notice = ref('')
let pendingSave: { payload: string; key: string } | null = null
let restoring = true
const tasks = useTaskRecovery(pid, 'research', current => {
  const result = current.result
  if (result?.kind === 'answer') answer.value = result
  if (result?.kind === 'evidence') evidence.value = result
  if (result?.kind === 'draft') {
    body.value = result.body; reportSources.value = result.sources; currentVersion.value = null; history.value = []
    if (restoring) question.value = ''
    notice.value = 'Mock 草稿已读取，尚未保存；请核对研究问题后保存。'
  }
})
onMounted(() => run(async () => { reports.value = (await api.reports(pid)).items }))
async function search() { await run(async () => { hits.value = (await api.search(pid, { query: question.value, mode: mode.value, year_from: yearFrom.value, year_to: yearTo.value, limit: 10 })).items; searched.value = true }) }
async function generate(kind: 'answer' | 'draft') {
  restoring = false
  await tasks.submit({ kind, question: question.value })
}
async function extract(paperId: string) {
  restoring = false
  await tasks.submit({ kind: 'evidence', paperId })
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
    notice.value = persistentData.value ? `已保存开发版本 ${currentVersion.value.number}，数据将在重启后保留。` : `已保存 Mock 版本 ${currentVersion.value.number}，后端重启后丢失。`
    history.value = (await api.versions(pid, currentVersion.value.report_id)).items
    reports.value = (await api.reports(pid)).items
  })
}
async function openReport(report: Models['ReportVersion']) { await run(async () => { history.value = (await api.versions(pid, report.report_id)).items; openVersion(history.value[history.value.length - 1]!) }) }
function openVersion(version: Models['ReportVersion']) { currentVersion.value = version; body.value = version.body; reportTitle.value = version.title; question.value = version.research_question; reportSources.value = version.sources; notice.value = `当前查看版本 ${version.number}` }
const evidenceLabels = { research_question: '研究问题', method: '研究方法', results: '实验结果', conclusion: '主要结论', limitations: '研究局限' } as const
</script>
<template>
  <div class="page-heading"><span class="eyebrow">EVIDENCE & SYNTHESIS</span><h1>研究工作台</h1><p class="muted">搜索、提问、展开来源，再把核验过的内容保存为版本。</p></div>
  <Feedback :busy="busy || tasks.busy.value" :error="error" />
  <TaskRecoveryPanel :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="tasks.refresh" @reconcile="reconcile" @retry="tasks.retryFailed" />
  <section class="panel"><el-input v-model="question" type="textarea" :rows="2" maxlength="2000" placeholder="输入研究问题" aria-label="研究问题" /><div class="toolbar"><el-radio-group v-model="mode" aria-label="检索模式"><el-radio-button value="keyword">关键词</el-radio-button><el-radio-button value="semantic">语义</el-radio-button><el-radio-button value="hybrid">组合</el-radio-button></el-radio-group><el-input-number v-model="yearFrom" placeholder="起始年份" aria-label="检索起始年份" :min="1000" :max="3000" /><el-input-number v-model="yearTo" placeholder="结束年份" aria-label="检索结束年份" :min="1000" :max="3000" /><el-button type="primary" :disabled="!question.trim() || busy || tasks.blocked.value" @click="search">检索</el-button><el-button :disabled="!question.trim() || busy || tasks.blocked.value" @click="generate('answer')">生成 Mock 回答</el-button><el-button :disabled="!question.trim() || busy || tasks.blocked.value" @click="generate('draft')">生成 Mock 草稿</el-button></div></section>
  <div class="research-grid"><div>
    <section v-if="answer" class="panel"><h2>证据问答 <el-tag type="warning">Mock</el-tag></h2><el-alert v-if="answer.insufficient_evidence" title="当前范围内证据不足" type="warning" :closable="false" /><p class="prewrap">{{ answer.answer }}</p><el-button v-for="source in answer.sources" :key="source.citation_id" text type="primary" @click="showSources([source])">查看引用 [{{ source.citation_id }}]</el-button></section>
    <section class="panel"><h2>检索片段</h2><el-empty v-if="!hits.length" :description="searched ? '当前范围内未找到相关内容' : '输入问题后开始检索'" :image-size="64" /><article v-for="hit in hits" :key="hit.source.chunk_id" class="hit"><h3>{{ hit.paper.title }}</h3><p class="muted">{{ hit.paper.year }} · 第 {{ hit.source.page_number }} 页 · 合成片段</p><p>{{ hit.source.excerpt }}</p><el-button text type="primary" @click="showSources([hit.source])">展开来源 [{{ hit.source.citation_id }}]</el-button><el-button text :disabled="busy || tasks.blocked.value" @click="extract(hit.paper.paper_id)">提取 Mock 证据</el-button></article></section>
    <section v-if="evidence" class="panel"><h2>结构化证据 · AI 提取，待核验（Mock）</h2><article v-for="(label, field) in evidenceLabels" :key="field" class="hit"><strong>{{ label }}</strong><el-tag size="small" type="info">{{ evidence[field].status }}</el-tag><p>{{ evidence[field].text || '未提及或证据不足' }}</p><el-button v-if="evidence[field].sources.length" text @click="showSources(evidence[field].sources)">支撑原文（合成）</el-button></article></section>
  </div><aside>
    <section class="panel"><h2>综述草稿与版本</h2><el-input v-model="reportTitle" placeholder="报告标题" aria-label="报告标题" /><el-input v-model="body" type="textarea" :rows="16" placeholder="生成草稿或在这里编辑报告" aria-label="报告正文" /><p class="muted">{{ notice || '保存版本时同时保存研究问题与引用快照。' }}</p><el-button type="primary" :disabled="!body.trim() || !reportTitle.trim() || !question.trim() || busy || tasks.busy.value" @click="save">保存新版本</el-button><el-button v-if="reportSources.length" @click="showSources(reportSources)">引用清单</el-button><p v-if="currentVersion"><a :href="`/api/v1/projects/${pid}/reports/${currentVersion.report_id}/versions/${currentVersion.version_id}/text`">导出当前版本文本</a></p><div v-if="history.length"><h3>历史版本</h3><el-button v-for="version in history" :key="version.version_id" size="small" @click="openVersion(version)">版本 {{ version.number }}</el-button></div></section>
    <section class="panel"><h2>已保存报告</h2><el-empty v-if="!reports.length" description="尚未保存报告" :image-size="48" /><p v-for="report in reports" :key="report.report_id"><el-button text type="primary" :disabled="tasks.busy.value" @click="openReport(report)">{{ report.title }} · v{{ report.number }}</el-button></p></section>
  </aside></div>
  <el-drawer v-model="sourceDrawer" title="引用来源 · 合成数据" size="min(560px, 95vw)"><SourcesPanel :sources="selectedSources" /></el-drawer>
</template>
