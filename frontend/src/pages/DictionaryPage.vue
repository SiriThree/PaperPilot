<script setup lang="ts">
import { computed, ref } from 'vue'
import dictionaryData from '../data/data-dictionary.json'
import AppIcon from '../components/AppIcon.vue'

interface DictionaryColumn { name: string; type: string; nullable: boolean; default: string | null }
interface DictionaryConstraint { kind: string; name: string; definition: string }
interface DictionaryIndex { name: string; definition: string }
interface DictionaryTable {
  name: string; group: string; description: string; row_count: number
  columns: DictionaryColumn[]; constraints: DictionaryConstraint[]; indexes: DictionaryIndex[]
}

const meta = dictionaryData as {
  exported_at: string; schema_version: number | null; table_count: number; source: string; tables: DictionaryTable[]
}
const tables = meta.tables

const GROUP_ORDER = ['身份与协作', '文献与内容', '向量与索引', '任务与结果', '报告与版本', '一致性与审计', '其他']
const KIND_LABELS: Record<string, string> = { primary_key: '主键', foreign_key: '外键', unique: '唯一约束', check: 'CHECK' }
const KIND_ORDER = ['primary_key', 'foreign_key', 'unique', 'check']

const query = ref('')
const selectedName = ref(tables.find(table => table.name === 'projects')?.name || tables[0]?.name || '')
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return tables
  return tables.filter(table => table.name.toLowerCase().includes(keyword) || table.description.toLowerCase().includes(keyword))
})
const grouped = computed(() => GROUP_ORDER
  .map(group => ({ group, items: filtered.value.filter(table => table.group === group) }))
  .filter(entry => entry.items.length))
const selected = computed(() => tables.find(table => table.name === selectedName.value) || null)
const selectedConstraints = computed(() => {
  const table = selected.value
  if (!table) return []
  return KIND_ORDER
    .map(kind => ({ kind, items: table.constraints.filter(constraint => constraint.kind === kind) }))
    .filter(entry => entry.items.length)
})
function exportTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
</script>

<template>
  <div class="business-page dictionary-page">
    <div class="business-heading"><div><span class="eyebrow">DATA DICTIONARY</span><h1>数据字典</h1><p>数据库共 {{ meta.table_count }} 张表：来源片段、任务结果、报告版本与审计事件如何组织，点表名即可查看结构与约束。</p></div><div class="workspace-principle"><AppIcon name="folder" :size="20" /><span>结构透明<br><strong>数据有据可查</strong></span></div></div>
    <section class="dictionary-meta" aria-label="字典快照信息">
      <div><span>模式版本</span><strong>schema v{{ meta.schema_version ?? '—' }}</strong></div>
      <div><span>数据表</span><strong>{{ meta.table_count }} 张</strong></div>
      <div><span>快照时间</span><strong>{{ exportTime(meta.exported_at) }}</strong></div>
      <div class="dictionary-meta-note"><AppIcon name="info" :size="15" /><span>{{ meta.source }}；页面为静态快照，不发起数据库请求。</span></div>
    </section>
    <div class="dictionary-layout">
      <nav class="work-panel dictionary-nav" aria-label="数据表列表">
        <el-input v-model="query" placeholder="搜索表名或说明…" aria-label="搜索数据表" clearable><template #prefix><AppIcon name="search" :size="15" /></template></el-input>
        <div v-if="!grouped.length" class="dictionary-empty">没有匹配的数据表。</div>
        <div v-for="entry in grouped" :key="entry.group" class="dictionary-group">
          <h2>{{ entry.group }}<span>{{ entry.items.length }}</span></h2>
          <button v-for="table in entry.items" :key="table.name" type="button" class="dictionary-table-button" :class="{ 'is-active': table.name === selectedName }" :aria-pressed="table.name === selectedName" @click="selectedName = table.name">
            <span class="dictionary-table-name">{{ table.name }}</span>
            <span class="dictionary-table-count">{{ table.row_count }}</span>
          </button>
        </div>
      </nav>
      <section v-if="selected" class="work-panel dictionary-detail" aria-label="表结构详情">
        <header class="dictionary-detail-heading">
          <div>
            <h2>{{ selected.name }}</h2>
            <p>{{ selected.description || '暂无说明。' }}</p>
          </div>
          <div class="dictionary-detail-facts">
            <span class="dictionary-tag">{{ selected.group }}</span>
            <span class="dictionary-tag">{{ selected.columns.length }} 列</span>
            <span class="dictionary-tag">导出时 {{ selected.row_count }} 行</span>
          </div>
        </header>
        <h3>字段</h3>
        <el-table :data="selected.columns" size="small" class="dictionary-columns">
          <el-table-column label="字段" min-width="150"><template #default="scope"><code>{{ scope.row.name }}</code></template></el-table-column>
          <el-table-column label="类型" min-width="130"><template #default="scope"><span class="dictionary-type">{{ scope.row.type }}</span></template></el-table-column>
          <el-table-column label="可空" width="70"><template #default="scope"><span :class="scope.row.nullable ? 'dictionary-nullable' : 'dictionary-notnull'">{{ scope.row.nullable ? '是' : '否' }}</span></template></el-table-column>
          <el-table-column label="默认值" min-width="120"><template #default="scope"><code v-if="scope.row.default" class="dictionary-default">{{ scope.row.default }}</code><span v-else class="dictionary-muted">—</span></template></el-table-column>
        </el-table>
        <template v-if="selectedConstraints.length">
          <h3>约束</h3>
          <div v-for="entry in selectedConstraints" :key="entry.kind" class="dictionary-constraint-group">
            <h4>{{ KIND_LABELS[entry.kind] || entry.kind }}<span>{{ entry.items.length }}</span></h4>
            <dl>
              <div v-for="constraint in entry.items" :key="constraint.name" class="dictionary-constraint">
                <dt><code>{{ constraint.name }}</code></dt>
                <dd><code>{{ constraint.definition }}</code></dd>
              </div>
            </dl>
          </div>
        </template>
        <details v-if="selected.indexes.length" class="dictionary-indexes">
          <summary>索引（{{ selected.indexes.length }}）</summary>
          <dl>
            <div v-for="index in selected.indexes" :key="index.name" class="dictionary-constraint">
              <dt><code>{{ index.name }}</code></dt>
              <dd><code>{{ index.definition }}</code></dd>
            </div>
          </dl>
        </details>
        <p class="muted dictionary-footer">快照由 <code>scripts/export_data_dictionary.py</code> 从开发数据库导出；结构以仓库内迁移与实际库为准，行数不代表实时数据量。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dictionary-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 26px; border: 1px solid var(--border); border-radius: 9px; background: #fff; padding: 15px 21px; margin-bottom: 22px; }
.dictionary-meta > div { display: flex; flex-direction: column; gap: 3px; }
.dictionary-meta span { color: var(--muted); font-size: 11px; }
.dictionary-meta strong { font-size: 14px; color: #25374d; }
.dictionary-meta-note { flex-direction: row !important; align-items: center; gap: 7px !important; margin-left: auto; max-width: 420px; }
.dictionary-meta-note span { font-size: 12px; line-height: 1.5; }
.dictionary-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 22px; align-items: start; }
.dictionary-nav { display: flex; flex-direction: column; gap: 15px; }
.dictionary-empty { color: var(--muted); font-size: 13px; }
.dictionary-group h2 { color: #25374d; font-size: 12px; letter-spacing: 1px; margin: 4px 0 8px; display: flex; align-items: center; gap: 7px; }
.dictionary-group h2 span { background: #eef4f1; border-radius: 999px; color: var(--muted); font-size: 11px; padding: 1px 8px; }
.dictionary-table-button { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 10px; border: 1px solid transparent; border-radius: 8px; background: transparent; cursor: pointer; padding: 8px 11px; text-align: left; }
.dictionary-table-button:hover { background: #f2f7f5; }
.dictionary-table-button.is-active { background: #176a5b; border-color: #176a5b; }
.dictionary-table-name { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; color: #25374d; overflow-wrap: anywhere; }
.dictionary-table-button.is-active .dictionary-table-name { color: #fff; }
.dictionary-table-count { color: var(--muted); font-size: 11.5px; }
.dictionary-table-button.is-active .dictionary-table-count { color: #cfe5df; }
.dictionary-detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; flex-wrap: wrap; margin-bottom: 6px; }
.dictionary-detail-heading h2 { color: #25374d; font-size: 20px; font-family: ui-monospace, Consolas, monospace; margin-bottom: 6px; }
.dictionary-detail-heading p { color: #7f8c9d; font-size: 13px; margin: 0; max-width: 560px; }
.dictionary-detail-facts { display: flex; gap: 8px; flex-wrap: wrap; }
.dictionary-tag { background: #eef4f1; border-radius: 999px; color: #176a5b; font-size: 12px; padding: 4px 11px; }
.dictionary-detail h3 { color: #25374d; font-size: 14px; margin: 20px 0 10px; }
.dictionary-columns code { font-size: 12px; }
.dictionary-type { color: #176a5b; font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.dictionary-nullable { color: var(--muted); }
.dictionary-notnull { color: #176a5b; font-weight: 600; }
.dictionary-default, .dictionary-constraint code { font-size: 11.5px; overflow-wrap: anywhere; }
.dictionary-muted { color: var(--muted); }
.dictionary-constraint-group h4 { color: var(--muted); font-size: 12px; margin: 12px 0 7px; display: flex; align-items: center; gap: 7px; }
.dictionary-constraint-group h4 span { background: #eef4f1; border-radius: 999px; padding: 0 8px; }
.dictionary-constraint { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 12px; border-top: 1px dashed var(--border); padding: 7px 0; }
.dictionary-constraint dt { margin: 0; }
.dictionary-constraint dd { margin: 0; color: #55636f; }
.dictionary-indexes { margin-top: 18px; }
.dictionary-indexes summary { color: #176a5b; cursor: pointer; font-size: 13px; }
.dictionary-indexes dl { margin: 10px 0 0; }
.dictionary-footer { margin: 20px 0 0; font-size: 12px; }
@media (max-width: 960px) {
  .dictionary-layout { grid-template-columns: 1fr; }
  .dictionary-constraint { grid-template-columns: 1fr; gap: 3px; }
  .dictionary-meta-note { margin-left: 0; }
}
</style>
