<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Models } from '../api/client'
import { useTaskRecovery } from '../composables/useTaskRecovery'
import TaskRecoveryPanel from '../components/TaskRecoveryPanel.vue'
import { useAction } from '../composables/useAction'
import { useResource } from '../composables/useResource'
import { pdfProblem, yearProblem } from '../validation'
import { aiMock, health, projects } from '../state'
import Feedback from '../components/Feedback.vue'
import SourcesPanel from '../components/SourcesPanel.vue'
import AppIcon from '../components/AppIcon.vue'
const route = useRoute()
const importOpen = ref(route.query.import === '1')
watch(() => route.query.import, value => { if (value === '1') importOpen.value = true })
const pid = String(route.params.projectId), { busy, error, run } = useAction()
const title = ref(''), chosen = ref<File | null>(null), yearFrom = ref<number>(), yearTo = ref<number>(), selectedPaperId = ref<string | null>(null)
const owner = computed(() => projects.value.find(project => project.project_id === pid)?.role === 'owner')
const paperResource = useResource<Models['Paper'][]>([], async () => (await api.papers(pid, yearFrom.value, yearTo.value)).items)
const detailResource = useResource<Models['PaperDetail'] | null>(null, async () => selectedPaperId.value ? api.paper(pid, selectedPaperId.value) : null)
const papers = paperResource.data, detail = detailResource.data
const fileError = ref(''), validatingFile = ref(false)
const yearError = computed(() => yearProblem(yearFrom.value, yearTo.value))
const rangeReady = computed(() => paperResource.loaded.value && !paperResource.loading.value && !paperResource.error.value)
const searchable = computed(() => papers.value.filter(paper => paper.status === 'searchable').length)
const statusLabels = { pending: '待处理', processing: '处理中', searchable: '可检索', failed: '处理失败' }
async function filter() { if (!yearError.value) await paperResource.load() }
const tasks = useTaskRecovery(pid, 'papers', async () => { await paperResource.load() })
onMounted(paperResource.load)
let selection = 0
async function choose(event: Event) {
  const id = ++selection, file = (event.target as HTMLInputElement).files?.[0]
  chosen.value = null; fileError.value = ''; validatingFile.value = !!file
  if (!file) return
  try {
    const problem = await pdfProblem(file)
    if (id !== selection) return
    fileError.value = problem
    if (!problem) { chosen.value = file; if (!title.value) title.value = file.name.replace(/\.pdf$/i, '').slice(0, 200) }
  } catch { if (id === selection) fileError.value = '暂时无法读取文件，请重新选择。' }
  finally { if (id === selection) validatingFile.value = false }
}
async function upload(reconcile = false) { await tasks.submit({ kind: 'process', file: chosen.value || undefined, title: title.value }, reconcile); if (tasks.task.value?.status === 'succeeded') importOpen.value = false }
async function show(paper: Models['Paper']) { selectedPaperId.value = paper.paper_id; await detailResource.load() }
function closeDetail() { selectedPaperId.value = null; detailResource.reset() }
async function remove(paper: Models['Paper']) {
  await run(async () => {
    await api.removePaper(pid, paper.paper_id)
    if (selectedPaperId.value === paper.paper_id) closeDetail()
    await paperResource.load()
  })
}
const titleQuery = ref(''), statusFilter = ref(''), sortOrder = ref('title'), page = ref(1)
const filteredPapers = computed(() => papers.value.filter(paper => paper.title.toLowerCase().includes(titleQuery.value.toLowerCase()) && (!statusFilter.value || paper.status === statusFilter.value)).sort((a, b) => sortOrder.value === 'year' ? (b.year ?? -1) - (a.year ?? -1) || a.title.localeCompare(b.title) : a.title.localeCompare(b.title)))
const visiblePapers = computed(() => filteredPapers.value.slice((page.value - 1) * 50, page.value * 50))
watch([titleQuery, statusFilter, sortOrder, papers], () => { page.value = 1 })
async function clearFilters() { titleQuery.value = ''; statusFilter.value = ''; yearFrom.value = undefined; yearTo.value = undefined; await paperResource.load() }
</script>
<template>
  <div class="business-page library-page compact-library">
    <div class="library-page-heading"><div><h1>文献工作台</h1><span>{{ rangeReady ? filteredPapers.length : '—' }} 篇文献</span></div><el-button type="primary" @click="importOpen = true"><AppIcon name="plus" :size="17" />导入文献</el-button></div>
    <Feedback :busy="busy" :error="error" />
    <TaskRecoveryPanel compact :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="tasks.refresh" @reconcile="importOpen = true" @retry="tasks.retryFailed" />
    <form class="library-filter-bar" @submit.prevent="filter"><el-input v-model="titleQuery" placeholder="搜索标题…" aria-label="搜索文献标题" clearable><template #prefix><AppIcon name="search" :size="17" /></template></el-input><el-select v-model="statusFilter" placeholder="全部状态" aria-label="筛选文献状态" clearable><el-option v-for="(label, value) in statusLabels" :key="value" :value="value" :label="label" /></el-select><el-input-number v-model="yearFrom" :min="1000" :max="3000" placeholder="起始年份" aria-label="起始年份" :controls="false" /><span>—</span><el-input-number v-model="yearTo" :min="1000" :max="3000" placeholder="结束年份" aria-label="结束年份" :controls="false" /><el-button native-type="submit" :disabled="paperResource.loading.value || !!yearError">筛选</el-button><el-select v-model="sortOrder" aria-label="文献排序"><el-option value="title" label="按标题排序" /><el-option value="year" label="年份从新到旧" /></el-select></form>
    <p v-if="yearError" class="field-error" role="alert">{{ yearError }}</p><Feedback :busy="paperResource.loading.value" :error="paperResource.error.value" retry-label="重新加载文献" @retry="paperResource.load" />
    <el-table v-if="filteredPapers.length" :data="visiblePapers" class="document-table" row-key="paper_id"><el-table-column label="标题" min-width="230"><template #default="scope"><RouterLink :to="`/projects/${pid}/research?paper=${scope.row.paper_id}`" class="library-paper-title"><AppIcon name="file" :size="19" /><strong>{{ scope.row.title }}</strong></RouterLink></template></el-table-column><el-table-column label="年份" width="100"><template #default="scope">{{ scope.row.year || '—' }}</template></el-table-column><el-table-column label="处理状态" width="120"><template #default="scope"><span class="paper-status" :class="`paper-state-${scope.row.status}`">{{ statusLabels[scope.row.status as keyof typeof statusLabels] }}</span></template></el-table-column><el-table-column label="操作" :width="owner ? 220 : 130"><template #default="scope"><div class="row-actions"><el-button text @click="show(scope.row)">详情</el-button><RouterLink :to="`/projects/${pid}/research?paper=${scope.row.paper_id}`">阅读</RouterLink><el-popconfirm v-if="owner" title="移出后不再参与检索；历史报告保留引用快照。确认移出？" confirm-button-text="确认移出" cancel-button-text="取消" :width="280" @confirm="remove(scope.row)"><template #reference><el-button text type="danger" :disabled="busy || tasks.busy.value" :aria-label="`移出文献 ${scope.row.title}`">移出项目</el-button></template></el-popconfirm></div></template></el-table-column></el-table>
    <el-pagination v-if="filteredPapers.length > 50" v-model:current-page="page" :page-size="50" :total="filteredPapers.length" layout="prev, pager, next, total" />
    <div v-if="paperResource.loaded.value && !paperResource.loading.value && !paperResource.error.value && !filteredPapers.length" class="desk-empty library-empty"><AppIcon name="book" :size="38" /><h2>当前范围内没有文献</h2><el-button v-if="titleQuery || statusFilter || yearFrom || yearTo" @click="clearFilters">清空筛选</el-button><el-button v-else type="primary" @click="importOpen = true">导入第一篇文献</el-button></div>
    <el-drawer v-model="importOpen" title="导入文献" size="min(460px, 95vw)" class="import-drawer"><form @submit.prevent="upload(false)"><label class="file-zone document-upload"><AppIcon name="upload" :size="30" /><strong>{{ chosen?.name || '选择 PDF 文件' }}</strong><span>单篇最多 20 MiB</span><input type="file" accept="application/pdf,.pdf" :aria-invalid="!!fileError" aria-describedby="file-error" aria-label="选择 PDF 文件" @change="choose"></label><p v-if="validatingFile" role="status">正在检查文件…</p><p v-if="fileError" id="file-error" class="field-error" role="alert">{{ fileError }}</p><label class="field-label" for="paper-title">论文标题</label><el-input id="paper-title" v-model="title" placeholder="论文标题" aria-label="论文标题" maxlength="200" /><p v-if="aiMock" class="import-mode">演示模式，仅模拟处理。</p><p v-if="tasks.message.value && tasks.task.value?.status !== 'succeeded'" role="status">{{ tasks.message.value }}</p><el-button v-if="tasks.record.value?.phase === 'unknown'" :disabled="tasks.busy.value" @click="upload(true)">使用原请求核对</el-button><el-button :aria-label="aiMock ? '上传并模拟处理' : '上传并处理'" type="primary" native-type="submit" class="upload-submit" :disabled="!chosen || validatingFile || !title.trim() || busy || tasks.blocked.value" :loading="tasks.busy.value">上传并处理</el-button></form></el-drawer>
    <el-drawer :model-value="!!selectedPaperId" title="论文详情与来源" class="source-drawer" size="min(620px, 95vw)" @close="closeDetail"><Feedback :busy="detailResource.loading.value" :error="detailResource.error.value" retry-label="重新加载详情" @retry="detailResource.load" /><template v-if="detail"><div class="paper-detail-heading"><h2>{{ detail.paper.title }}</h2><span>{{ detail.paper.year || '年份未标注' }} · {{ statusLabels[detail.paper.status] }}</span></div><SourcesPanel :sources="detail.sources" /></template></el-drawer>
  </div>
</template>
