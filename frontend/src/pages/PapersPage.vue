<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Models } from '../api/client'
import { useTaskRecovery } from '../composables/useTaskRecovery'
import TaskRecoveryPanel from '../components/TaskRecoveryPanel.vue'
import { useAction } from '../composables/useAction'
import Feedback from '../components/Feedback.vue'
import SourcesPanel from '../components/SourcesPanel.vue'
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
const papers = ref<Models['Paper'][]>([]), detail = ref<Models['PaperDetail'] | null>(null), title = ref(''), chosen = ref<File | null>(null), yearFrom = ref<number>(), yearTo = ref<number>()
const tasks = useTaskRecovery(pid, 'papers', () => load())
async function load() { papers.value = (await api.papers(pid, yearFrom.value, yearTo.value)).items }
onMounted(() => run(load))
function choose(event: Event) { chosen.value = (event.target as HTMLInputElement).files?.[0] || null; if (chosen.value && !title.value) title.value = chosen.value.name.replace(/\.pdf$/i, '') }
async function upload(reconcile = false) { await tasks.submit({ kind: 'process', file: chosen.value || undefined, title: title.value }, reconcile) }
async function show(paper: Models['Paper']) { await run(async () => { detail.value = await api.paper(pid, paper.paper_id) }) }
</script>
<template>
  <div class="page-heading"><span class="eyebrow">LIBRARY</span><h1>文献工作台</h1><p class="muted">导入论文，查看处理状态与来源片段。</p></div>
  <Feedback :busy="busy || tasks.busy.value" :error="error" />
  <TaskRecoveryPanel :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="tasks.refresh" @reconcile="upload(true)" @retry="tasks.retryFailed" />
  <div class="workspace-grid"><section class="panel"><h2>导入文献</h2><p class="muted">单篇 PDF，最多 20 MiB。当前仅模拟处理，不提取真实正文。</p><label class="file-zone">选择 PDF 文件<input type="file" accept="application/pdf,.pdf" aria-label="选择 PDF 文件" @change="choose"></label><el-input v-model="title" placeholder="论文标题" aria-label="论文标题" maxlength="200" /><el-button type="primary" :disabled="!chosen || !title.trim() || busy || tasks.blocked.value" :loading="tasks.busy.value" @click="upload(false)">上传并模拟处理</el-button></section>
  <section class="panel"><div class="section-top"><h2>项目文献</h2><el-tag effect="plain">{{ papers.length }} 篇 · Mock</el-tag></div><div class="inline-form"><el-input-number v-model="yearFrom" :min="1000" :max="3000" placeholder="起始年份" aria-label="起始年份" /><el-input-number v-model="yearTo" :min="1000" :max="3000" placeholder="结束年份" aria-label="结束年份" /><el-button @click="run(load)">筛选</el-button></div><el-table v-if="papers.length" :data="papers"><el-table-column prop="title" label="标题" min-width="190" /><el-table-column prop="year" label="年份" width="85" /><el-table-column prop="status" label="处理状态" width="110" /><el-table-column label="来源" width="90"><template #default="scope"><el-button text type="primary" @click="show(scope.row)">详情</el-button></template></el-table-column></el-table><el-empty v-else-if="!busy" description="当前范围内没有文献" /></section></div>
  <el-drawer :model-value="!!detail" title="论文详情与合成来源" size="min(560px, 95vw)" @close="detail = null"><template v-if="detail"><h2>{{ detail.paper.title }}</h2><p class="muted">{{ detail.paper.year }} · {{ detail.paper.status }}</p><SourcesPanel :sources="detail.sources" /></template></el-drawer>
</template>
