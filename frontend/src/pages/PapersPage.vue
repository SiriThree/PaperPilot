<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
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
async function upload(reconcile = false) { await tasks.submit({ kind: 'process', file: chosen.value || undefined, title: title.value }, reconcile) }
async function show(paper: Models['Paper']) { selectedPaperId.value = paper.paper_id; await detailResource.load() }
function closeDetail() { selectedPaperId.value = null; detailResource.reset() }
async function remove(paper: Models['Paper']) {
  await run(async () => {
    await api.removePaper(pid, paper.paper_id)
    if (selectedPaperId.value === paper.paper_id) closeDetail()
    await paperResource.load()
  })
}
</script>
<template>
  <div class="business-page library-page">
    <div class="business-heading">
      <div><span class="eyebrow">LITERATURE LIBRARY</span><h1>文献工作台</h1><p>从一篇论文开始，建立你的研究文献库。</p></div>
      <div class="heading-summary"><span class="summary-icon"><AppIcon name="book" :size="23" /></span><div><strong>{{ rangeReady ? papers.length : '—' }}<small>篇文献</small></strong><span>当前筛选范围 · {{ rangeReady ? searchable : '—' }} 篇可检索</span></div></div>
    </div>
    <Feedback :busy="busy" :error="error" />
    <TaskRecoveryPanel :records="tasks.records.value" :selected="tasks.selected.value" :task="tasks.task.value" :busy="tasks.busy.value" :message="tasks.message.value" :storage-error="tasks.storageError.value" @query="tasks.refresh" @reconcile="upload(true)" @retry="tasks.retryFailed" />
    <div class="library-layout">
      <section class="work-panel import-panel">
        <div class="work-panel-heading"><span class="work-icon"><AppIcon name="upload" /></span><div><h2>导入文献</h2><p>为项目添加新的研究材料</p></div></div>
        <div class="import-content">
          <div>
            <label class="file-zone document-upload">
              <span class="upload-glyph"><AppIcon name="upload" :size="27" /></span>
              <strong>选择 PDF 文件</strong><span>点击选择 · 单篇最多 20 MiB</span>
              <input type="file" accept="application/pdf,.pdf" :aria-invalid="!!fileError" aria-describedby="file-error" aria-label="选择 PDF 文件" @change="choose">
            </label>
            <p v-if="validatingFile" role="status" class="muted">正在检查文件…</p>
            <p v-if="fileError" id="file-error" class="field-error" role="alert">{{ fileError }}</p>
            <div v-if="chosen" class="selected-file"><AppIcon name="file" :size="18" /><div><strong>{{ chosen.name }}</strong><span>{{ Math.max(1, Math.ceil(chosen.size / 1024)) }} KiB · 已选择</span></div></div>
          </div>
          <form class="upload-form" @submit.prevent="upload(false)">
            <label class="field-label" for="paper-title">论文标题</label>
            <el-input id="paper-title" v-model="title" placeholder="论文标题" aria-label="论文标题" maxlength="200" />
            <p class="field-caption">用于项目内的文献识别，可在上传前调整。</p>
            <el-button type="primary" native-type="submit" class="upload-submit" :disabled="!chosen || validatingFile || !title.trim() || busy || tasks.blocked.value" :loading="tasks.busy.value"><AppIcon name="upload" :size="17" />{{ aiMock ? '上传并模拟处理' : '上传并处理' }}</el-button>
          </form>
        </div>
        <div class="import-boundary"><AppIcon name="info" :size="16" /><p>{{ aiMock ? '当前仅模拟处理，不提取真实正文。' : '处理模块需按实际结果验收，注册不代表解析质量已验证。' }}<span>上传后可在任务区域查询处理进度。</span></p></div>
      </section>
      <section class="work-panel library-list">
        <div class="work-panel-heading"><span class="work-icon"><AppIcon name="book" /></span><div><h2>项目文献</h2><p>浏览论文，回到原文片段核对来源</p></div><span class="provenance-badge">{{ health?.modes.a === 'mock' ? 'Mock 数据' : health ? '数据适配器 · 待验收' : '模式未知' }}</span></div>
        <form class="range-toolbar" @submit.prevent="filter">
          <span class="toolbar-label"><AppIcon name="filter" :size="15" />年份范围</span>
          <el-input-number v-model="yearFrom" :min="1000" :max="3000" placeholder="起始年份" aria-label="起始年份" :controls="false" />
          <span class="range-separator">—</span>
          <el-input-number v-model="yearTo" :min="1000" :max="3000" placeholder="结束年份" aria-label="结束年份" :controls="false" />
          <el-button native-type="submit" :disabled="paperResource.loading.value || !!yearError">筛选</el-button>
        </form>
        <p v-if="yearError" class="field-error" role="alert">{{ yearError }}</p>
        <Feedback :busy="paperResource.loading.value" :error="paperResource.error.value" retry-label="重新加载文献" @retry="paperResource.load" />
        <el-table v-if="papers.length" :data="papers" class="document-table" row-key="paper_id">
          <el-table-column label="标题" min-width="220"><template #default="scope"><div class="document-title-cell"><span class="document-type">PDF</span><div><strong>{{ scope.row.title }}</strong><small>项目文献 · {{ scope.row.mock ? 'Mock' : '待核验' }}</small></div></div></template></el-table-column>
          <el-table-column label="年份" width="82"><template #default="scope"><span class="table-secondary">{{ scope.row.year ?? '—' }}</span></template></el-table-column>
          <el-table-column label="处理状态" width="112"><template #default="scope"><el-tag effect="light" :type="scope.row.status === 'failed' ? 'danger' : scope.row.status === 'searchable' ? 'success' : 'info'">{{ statusLabels[scope.row.status as keyof typeof statusLabels] || scope.row.status }}</el-tag></template></el-table-column>
          <el-table-column label="操作" :width="owner ? 175 : 84"><template #default="scope"><div class="row-actions"><el-button text type="primary" @click="show(scope.row)">详情</el-button><el-popconfirm v-if="owner" title="移出后不再参与检索；历史报告保留引用快照。确认移出？" confirm-button-text="确认移出" cancel-button-text="取消" :width="280" @confirm="remove(scope.row)"><template #reference><el-button text type="danger" :disabled="busy || tasks.busy.value" :aria-label="`移出文献 ${scope.row.title}`">移出项目</el-button></template></el-popconfirm></div></template></el-table-column>
        </el-table>
        <el-empty v-if="paperResource.loaded.value && !paperResource.loading.value && !paperResource.error.value && !papers.length" description="当前范围内没有文献" :image-size="72"><span class="empty-guidance">导入一篇 PDF，开始整理文献与来源。</span></el-empty>
        <div class="table-footnote"><AppIcon name="info" :size="14" /><span>来源片段与页码可从「详情」查看。移出文献会影响后续检索，历史引用快照保留。</span></div>
      </section>
    </div>
    <el-drawer :model-value="!!selectedPaperId" title="论文详情与来源" class="source-drawer" size="min(620px, 95vw)" @close="closeDetail">
      <Feedback :busy="detailResource.loading.value" :error="detailResource.error.value" retry-label="重新加载详情" @retry="detailResource.load" />
      <template v-if="detail"><div class="paper-detail-heading"><span class="document-type">PDF</span><h2>{{ detail.paper.title }}</h2><div class="detail-metadata"><span>{{ detail.paper.year ?? '年份未标注' }}</span><el-tag type="info">{{ statusLabels[detail.paper.status] }}</el-tag><span class="provenance-badge">{{ detail.paper.mock ? 'Mock' : '适配器数据，待验收' }}</span></div></div><SourcesPanel :sources="detail.sources" /></template>
    </el-drawer>
  </div>
</template>
