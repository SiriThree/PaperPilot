<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Models } from '../api/client'
import { projects } from '../state'
import { useAction } from '../composables/useAction'
import Feedback from '../components/Feedback.vue'
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
const owner = computed(() => projects.value.find(p => p.project_id === pid)?.role === 'owner')
const question = ref('按年份统计论文数量'), result = ref<Models['SecureQueryResult'] | null>(null)
const audits = ref<Models['AuditEvent'][]>([]), total = ref(0), page = ref(1), action = ref(''), decision = ref(''), actorId = ref(''), since = ref(''), until = ref('')
async function load() { const data = await api.audit(pid, { action: action.value || undefined, decision: decision.value || undefined, actor_id: actorId.value || undefined, since: since.value || undefined, until: until.value || undefined, offset: (page.value-1)*10, limit: 10 }); audits.value = data.items; total.value = data.total }
async function query() { result.value = null; await run(async () => { result.value = await api.secureQuery(pid, question.value); if (owner.value) await load() }) }
onMounted(() => { if (owner.value) void run(load) })
</script>
<template>
  <div class="page-heading"><span class="eyebrow">QUERY & TRACE</span><h1>查询与审计</h1><p class="muted">查看受支持统计问题的演示结果，以及请求决策的关联记录。</p></div>
  <el-alert title="Mock 网关仅匹配演示意图，未生成或执行 SQL，也不验证真实 SQL 攻击防护。" type="warning" :closable="false" show-icon />
  <Feedback :busy="busy" :error="error" />
  <section class="panel"><h2>自然语言统计查询</h2><el-input v-model="question" aria-label="统计问题" placeholder="输入统计问题" maxlength="2000" /><div class="toolbar"><el-button type="primary" :loading="busy" :disabled="!question.trim()" @click="query">提交 Mock 查询</el-button><el-button v-for="sample in ['按年份统计论文数量','按研究主题统计论文数量','查询尚未完成证据提取的论文','模拟执行失败','模拟网关故障','不支持的问题']" :key="sample" size="small" @click="question = sample">{{ sample }}</el-button></div><template v-if="result"><div class="toolbar"><el-tag :type="result.execution_status === 'executed' ? 'success' : 'warning'">{{ result.decision }} / {{ result.execution_status }}</el-tag><small class="muted">{{ result.request_id }}</small></div><el-alert v-if="result.reason" :title="`${result.reason.message}（${result.reason.code}）`" :type="result.execution_status === 'failed' ? 'error' : 'warning'" :closable="false" /><el-table v-if="result.execution_status === 'executed' && result.rows?.length" :data="result.rows"><el-table-column v-for="column in result.columns" :key="column" :prop="column" :label="column" /></el-table><el-empty v-else-if="result.execution_status === 'executed'" description="查询执行演示完成，当前范围无结果" :image-size="48" /></template></section>
  <section class="panel"><h2>项目审计 <el-tag effect="plain">Mock 内存记录</el-tag></h2><el-alert v-if="!owner" title="完整项目审计仅负责人可访问，服务端同样执行角色校验。" type="info" :closable="false" /><template v-else><div class="filter-grid"><el-input v-model="action" placeholder="事件类型，例如 secure_query.finished" aria-label="审计事件类型" /><el-select v-model="decision" clearable placeholder="全部决策" aria-label="审计决策"><el-option v-for="d in ['allowed','rejected','undetermined']" :key="d" :value="d" :label="d" /></el-select><el-input v-model="actorId" placeholder="用户 UUID（可选）" aria-label="审计用户" /><el-input v-model="since" placeholder="起始时间 ISO 8601，带时区" aria-label="审计起始时间" /><el-input v-model="until" placeholder="结束时间 ISO 8601，带时区" aria-label="审计结束时间" /><el-button :disabled="busy" @click="page = 1; run(load)">筛选审计</el-button></div><el-table v-if="audits.length" :data="audits"><el-table-column prop="timestamp" label="时间" min-width="180" /><el-table-column prop="action" label="事件" min-width="170" /><el-table-column prop="decision" label="决策" width="120" /><el-table-column prop="execution_status" label="执行状态" width="120" /><el-table-column prop="request_id" label="请求编号" min-width="250" /></el-table><el-empty v-else-if="!busy" description="当前筛选条件下没有审计事件" :image-size="64" /><el-pagination v-model:current-page="page" :total="total" :page-size="10" layout="prev, pager, next, total" @current-change="run(load)" /></template></section>
</template>
