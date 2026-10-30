<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError, type Models } from '../api/client'
import { formatQueryReason } from '../api/errors'
import { gatewayMock, health, user } from '../state'
import { useAction } from '../composables/useAction'
import { useResource } from '../composables/useResource'
import Feedback from '../components/Feedback.vue'
import AppIcon from '../components/AppIcon.vue'
import SecurityEvidencePanel from '../components/SecurityEvidencePanel.vue'
import SecurityExperimentPanel from '../components/SecurityExperimentPanel.vue'
const pid = String(useRoute().params.projectId), { busy, error, run } = useAction()
const scope = useResource<Models['Project'] | null>(null, async () => {
  const project = (await api.projects()).items.find(value => value.project_id === pid)
  if (!project) throw new ApiError('RESOURCE_NOT_FOUND', '当前项目不存在或不可访问')
  return project
})
const owner = computed(() => scope.data.value?.role === 'owner')
onMounted(scope.load)
const identityMode = computed(() => health.value?.limitations.includes('authentication_credentials') ? '凭据认证' : health.value?.limitations.includes('development_identity_only') ? '开发演示身份' : '认证模式待确认')
const queryCaption = ref('')
const question = ref('按年份统计论文数量'), result = ref<Models['SecureQueryResult'] | null>(null)
const querySummary = computed(() => !result.value ? '' : result.value.decision === 'rejected' ? '查询被策略拒绝，未执行' : result.value.execution_status === 'executed' ? (result.value.mock ? '演示结果已返回，未执行真实 SQL' : '网关报告已执行，实际安全能力待验收') : result.value.execution_status === 'failed' ? '查询执行失败，没有可用结果' : '尚未确认执行，策略通过不等于执行成功')
function clearQuery() { if (!busy.value) { result.value = null; error.value = ''; queryCaption.value = '' } }
const page = ref(1), action = ref(''), decision = ref(''), actorId = ref(''), since = ref(''), until = ref('')
const auditResource = useResource<Models['AuditList']>({ items: [], total: 0, offset: 0, limit: 10, mock: true }, () => api.audit(pid, { action: action.value || undefined, decision: decision.value || undefined, actor_id: actorId.value || undefined, since: since.value || undefined, until: until.value || undefined, offset: (page.value-1)*10, limit: 10 }))
const audits = computed(() => auditResource.data.value.items), total = computed(() => auditResource.data.value.total)
async function query() { await run(async () => { result.value = null; if (!await scope.load()) return; queryCaption.value = question.value; result.value = await api.secureQuery(pid, queryCaption.value); if (owner.value) await auditResource.load() }) }
watch(owner, value => { if (value) void auditResource.load(); else { auditResource.reset(); auditDetailOpen.value = false; selectedAudit.value = null } })
const decisionLabels: Record<string, string> = { allowed: '允许', rejected: '拒绝', undetermined: '未判定' }
const executionLabels: Record<string, string> = { executed: '已执行', not_executed: '未执行', failed: '失败', pending: '处理中', completed: '已完成', succeeded: '已完成', cancelled: '已取消' }
const actionLabels: Record<string, string> = {
  'secure_query.requested': '提交统计查询', 'secure_query.finished': '统计查询返回',
  'security.query.prepared': 'SQL 校验通过，准备执行', 'security.query.finished': 'SQL 处理结束',
  'paper.import_requested': '请求导入论文', 'paper.imported': '论文已导入，解析由任务处理',
  'paper.remove_requested': '请求移出论文', 'paper.removed': '论文已移出项目',
  'evidence.requested': '请求提取论文证据', 'answer.requested': '请求生成文献问答',
  'process.completed': 'PDF 解析处理已完成', 'evidence.completed': '论文证据提取已完成',
  'answer.completed': '文献问答已生成', 'draft.completed': '报告草稿已生成',
  'draft.requested': '请求生成报告草稿', 'report.save_requested': '请求保存报告',
  'report.saved': '报告版本已保存', 'report.exported': '读取报告导出内容',
  'task.retry_requested': '请求重试任务', 'project.created': '创建项目',
  'member.added': '添加项目成员', 'member.removed': '移除项目成员',
  import_paper: '论文导入已提交', remove_paper: '论文移出已提交',
  create_task: '创建处理任务', complete_job: '任务完成', fail_job: '任务失败',
  retry_task: '任务重试已提交', save_report: '报告保存已提交',
  create_project: '项目创建已提交', add_member: '成员添加已提交', remove_member: '成员移除已提交',
}
const resourceLabels: Record<string, string> = { paper: '论文', secure_query: '统计查询', evidence: '证据', answer: '问答', draft: '报告草稿', report: '报告／版本', task: '任务', project: '项目', member: '成员', session: '登录会话' }
const selectedAudit = ref<Models['AuditEvent'] | null>(null), auditDetailOpen = ref(false)
function showAudit(event: Models['AuditEvent']) { selectedAudit.value = event; auditDetailOpen.value = true }
function hasAuditDetails(event: Models['AuditEvent']) { return !!(event.details?.question || event.details?.sql || event.details?.resource_name || event.details?.resource_ids?.length) }
async function resetAuditFilters() {
  action.value = ''; decision.value = ''; actorId.value = ''; since.value = ''; until.value = ''; page.value = 1
  await auditResource.load()
}
function eventTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date)
}
</script>
<template>
  <div class="business-page security-page">
    <div class="business-heading"><div><span class="eyebrow">QUERY & TRACE</span><h1>查询与审计</h1><p>先确认访问范围，再查询数据，最后追溯具体操作。</p></div><nav class="security-page-nav" aria-label="查询与审计页内导航"><a href="#query-workspace">统计查询</a><a href="#project-audit">操作审计</a><a href="#security-evidence">离线验证</a><a href="#security-experiment">攻防实验</a></nav></div>
    <section class="security-scope" aria-label="当前访问范围"><div><span>当前身份</span><strong>{{ user?.display_name || '未登录' }}</strong><small>{{ identityMode }}</small></div><div><span>项目角色</span><strong data-testid="scope-role">{{ scope.data.value?.role === 'owner' ? '负责人' : scope.data.value?.role === 'member' ? '成员' : '待确认' }}</strong></div><div><span>当前项目</span><strong data-testid="scope-project">{{ scope.data.value?.name || '未确认访问范围' }}</strong></div><div><span>访问范围</span><strong>{{ scope.loaded.value ? '当前项目' : '待服务端确认' }}</strong><small>{{ owner ? '可查询、管理成员、移出论文和查看审计' : scope.data.value?.role === 'member' ? '可参与文献研究与查询；成员管理、移出论文和完整审计仅负责人可用' : '访问权限未确认，暂不可提交查询' }}</small></div><el-button :disabled="scope.loading.value" @click="scope.load"><AppIcon name="refresh" :size="14" />核对访问范围</el-button></section>
    <Feedback :busy="scope.loading.value" :error="scope.error.value" retry-label="重新核对访问范围" @retry="scope.load" />
    <div class="gateway-notice"><AppIcon name="info" :size="18" /><p>{{ gatewayMock ? 'Mock 网关仅匹配演示意图，未生成或执行 SQL，也不验证真实 SQL 攻击防护。' : health ? '已注册安全适配器；注册和接口返回不代表 SQL 安全防护已通过验收。' : '安全适配器运行模式未知；所有查询仍由服务端网关处理。' }}</p></div>
    <p v-if="!gatewayMock" class="gateway-notice">当前使用三类固定模板降级，不调用模型生成 SQL；模板与模型查询分别记录和评估。超出范围的问题会被拒绝。</p>
    <ol class="gateway-pipeline" aria-label="安全查询调用链路示意"><li><span>01</span><div><strong>身份与项目检查</strong><small>服务端验证当前请求</small></div></li><li><span>02</span><div><strong>安全网关</strong><small>{{ gatewayMock ? '当前为 Mock 意图演示' : '由已注册适配器处理' }}</small></div></li><li :class="{ 'pipeline-active': !!result || !!error }"><span>03</span><div><strong>执行结果</strong><small>{{ busy ? '等待响应' : result ? `${decisionLabels[result.decision]} · ${result.mock ? '模拟' : ''}${executionLabels[result.execution_status]}` : error ? '未取得可用结果' : '以实际响应为准' }}</small></div></li><li><span>04</span><div><strong>审计记录</strong><small>通过请求编号关联</small></div></li></ol>
    <Feedback :busy="busy" :error="error" />
    <div id="query-workspace" class="query-workspace">
      <section class="work-panel query-composer"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="search" /></span><div><h2>自然语言统计查询</h2><p>描述统计问题，由现有安全网关处理</p></div></div>
        <form @submit.prevent="query"><label class="field-label" for="statistics-question">统计问题</label><el-input id="statistics-question" v-model="question" :disabled="busy" aria-label="统计问题" placeholder="输入统计问题" maxlength="2000" /><div class="query-actions"><el-button type="primary" native-type="submit" :loading="busy" :disabled="!question.trim() || busy || !scope.loaded.value"><AppIcon name="search" :size="16" />{{ gatewayMock ? '提交 Mock 查询' : '提交查询' }}</el-button><el-button :disabled="busy || (!result && !error)" @click="clearQuery">清除查询结果</el-button><span>结果仅在收到明确响应后展示</span></div></form>
        <div class="query-suggestions"><span>试试这些问题</span><div><button v-for="sample in ['按年份统计论文数量','按研究主题统计论文数量','查询尚未完成证据提取的论文']" :key="sample" :disabled="busy" @click="question = sample">{{ sample }}<AppIcon name="arrow" :size="12" /></button></div></div><details v-if="gatewayMock" class="simulation-examples"><summary>异常场景演示</summary><div><el-button v-for="sample in ['模拟执行失败','模拟网关故障','不支持的问题']" :key="sample" size="small" :disabled="busy" @click="question = sample">{{ sample }}</el-button></div></details>
        <el-button v-if="!gatewayMock" size="small" :disabled="busy" @click="question = '不支持的问题'">不支持的问题</el-button>
        <details class="simulation-examples"><summary>越权拒绝演示</summary><p>负责人复制当前页面地址，退出后以无该项目权限的账户登录，再打开该地址。访问应被拒绝；项目编号和统计问题不能授予访问权限。请勿把其他项目的数据作为正常查询结果展示。</p></details>
      </section>
    <section class="work-panel query-result-panel" aria-label="查询结果"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="file" /></span><div><h2>查询结果</h2><p>{{ result ? '本次请求的决策与返回结果' : '提交后在这里查看查询状态' }}</p></div><span v-if="result" class="provenance-badge">{{ result.mock ? 'Mock 演示结果' : '适配器返回结果，待验收' }}</span></div>
      <template v-if="result"><div class="query-result-summary" :class="`query-status-${result.execution_status}`"><span class="query-result-icon"><AppIcon :name="result.execution_status === 'executed' ? 'check' : 'info'" :size="20" /></span><div><p role="status">{{ querySummary }}</p><span>{{ queryCaption }}</span></div><el-tag :type="result.execution_status === 'executed' ? 'success' : result.execution_status === 'failed' ? 'danger' : 'warning'">{{ result.decision }} / {{ result.execution_status }}</el-tag></div><el-alert v-if="result.reason" :title="formatQueryReason(result.reason)" :type="result.execution_status === 'failed' ? 'error' : 'warning'" :closable="false" />
        <dl class="query-outcome"><div><dt>策略决策</dt><dd>{{ decisionLabels[result.decision] }}</dd></div><div><dt>执行状态</dt><dd>{{ executionLabels[result.execution_status] }}</dd></div><div v-if="result.execution_status === 'executed'"><dt>返回行数</dt><dd>{{ result.rows?.length || 0 }}</dd></div></dl>
        <el-table v-if="result.execution_status === 'executed' && result.rows?.length" :data="result.rows" class="query-result-table"><el-table-column v-for="column in result.columns" :key="column" :prop="column" :label="column" /></el-table><el-empty v-else-if="result.execution_status === 'executed'" description="查询已完成，当前范围无结果" :image-size="48" /><div class="query-correlation"><span>请求编号</span><code>{{ result.request_id }}</code><small>用于关联本次查询与审计记录</small></div>
      </template><div v-else-if="busy" class="query-pending" role="status"><AppIcon name="clock" :size="24" /><span>正在等待网关响应，请保留当前请求。</span></div><el-empty v-else-if="!error" description="输入统计问题后提交查询" :image-size="58" /><p v-else class="query-unavailable">本次请求没有可展示的结果。请按上方错误提示处理后，明确重试。</p>
    </section>
    </div>
    <section id="project-audit" class="work-panel audit-panel"><div class="work-panel-heading"><span class="work-icon"><AppIcon name="clock" /></span><div><h2>项目审计</h2><p>查看操作、目标资源及查询内容；请求已提交与操作已完成分别记录。</p></div><span v-if="auditResource.loaded.value" class="provenance-badge">{{ auditResource.data.value.mock ? 'Mock 记录' : '适配器记录，待验收' }}</span></div><el-alert v-if="scope.loaded.value && !owner" title="完整项目审计仅负责人可访问，服务端同样执行角色校验。" type="info" :closable="false" />
      <template v-if="owner"><form class="audit-filters" @submit.prevent="page = 1; auditResource.load()"><div><label for="audit-action" class="field-label">事件类型</label><el-input id="audit-action" v-model="action" placeholder="例如 secure_query.finished" aria-label="审计事件类型" /></div><div><label for="audit-decision" class="field-label">决策结果</label><el-select id="audit-decision" v-model="decision" clearable placeholder="全部决策" aria-label="审计决策"><el-option v-for="d in ['allowed','rejected','undetermined']" :key="d" :value="d" :label="`${decisionLabels[d]} · ${d}`" /></el-select></div><div><label for="audit-actor" class="field-label">用户 UUID</label><el-input id="audit-actor" v-model="actorId" placeholder="可选，按用户编号筛选" aria-label="审计用户" /></div><div><label for="audit-since" class="field-label">起始时间</label><el-input id="audit-since" v-model="since" placeholder="ISO 8601，带时区" aria-label="审计起始时间" /></div><div><label for="audit-until" class="field-label">结束时间</label><el-input id="audit-until" v-model="until" placeholder="ISO 8601，带时区" aria-label="审计结束时间" /></div><div class="audit-filter-submit"><el-button native-type="submit" :disabled="auditResource.loading.value"><AppIcon name="filter" :size="15" />筛选审计</el-button><el-button :disabled="auditResource.loading.value" @click="resetAuditFilters">清空筛选</el-button></div></form>
        <Feedback :busy="auditResource.loading.value" :error="auditResource.error.value" retry-label="重新加载审计" @retry="auditResource.load" />
        <el-table v-if="audits.length" :data="audits" class="audit-table" row-key="event_id"><el-table-column label="操作者" min-width="150"><template #default="cell"><span>{{ cell.row.actor_id === user?.user_id ? user?.display_name : cell.row.actor_id ? "用户 " + cell.row.actor_id.slice(0, 8) : "系统" }}</span><small class="audit-actor-id">{{ cell.row.actor_id || "系统事件" }}</small></template></el-table-column><el-table-column label="所属项目" min-width="175"><template #default="cell"><span>{{ cell.row.project_id === pid ? scope.data.value?.name : "未确认项目" }}</span></template></el-table-column><el-table-column label="事件时间" min-width="170"><template #default="scope"><time :datetime="scope.row.timestamp" :title="scope.row.timestamp">{{ eventTime(scope.row.timestamp) }}</time></template></el-table-column><el-table-column label="具体操作" min-width="240"><template #default="cell"><strong class="audit-operation">{{ actionLabels[cell.row.action] || cell.row.action }}</strong><small class="audit-action-code">{{ cell.row.action }}</small><p class="audit-operation-preview">{{ cell.row.details?.resource_name || cell.row.details?.question || (cell.row.details?.sql ? '已记录校验后的 SQL' : cell.row.details?.resource_ids?.length ? `已记录 ${cell.row.details.resource_ids.length} 个目标编号` : '未记录操作详情') }}</p><el-button link type="primary" @click="showAudit(cell.row)">查看详情</el-button></template></el-table-column><el-table-column label="决策" width="115"><template #default="scope"><span class="audit-decision" :class="`decision-${scope.row.decision}`">{{ decisionLabels[scope.row.decision] || scope.row.decision }}</span></template></el-table-column><el-table-column label="执行状态" width="105"><template #default="scope"><span class="audit-execution" :class="`execution-${scope.row.execution_status}`">{{ executionLabels[scope.row.execution_status] || scope.row.execution_status }}</span></template></el-table-column><el-table-column label="请求编号" min-width="245"><template #default="scope"><code class="audit-request-id">{{ scope.row.request_id }}</code></template></el-table-column></el-table>
        <el-empty v-if="auditResource.loaded.value && !auditResource.error.value && !audits.length" description="当前筛选条件下没有审计事件" :image-size="64" /><div class="audit-footer"><span>事件时间按浏览器本地时区展示 · 请求编号可选中复制</span><el-pagination v-model:current-page="page" :total="total" :page-size="10" layout="prev, pager, next, total" @current-change="auditResource.load" /></div>
      </template>
    </section>
    <SecurityEvidencePanel id="security-evidence" :real="!gatewayMock" />
    <SecurityExperimentPanel />
    <el-dialog v-model="auditDetailOpen" title="审计操作详情" width="720px" class="audit-detail-dialog">
      <template v-if="selectedAudit">
        <p class="audit-detail-title">{{ actionLabels[selectedAudit.action] || selectedAudit.action }}</p>
        <dl class="audit-detail-meta">
          <div><dt>原始事件</dt><dd><code>{{ selectedAudit.action }}</code></dd></div>
          <div><dt>资源类型</dt><dd>{{ resourceLabels[selectedAudit.resource_type] || selectedAudit.resource_type }}</dd></div>
          <div><dt>事件时间</dt><dd>{{ eventTime(selectedAudit.timestamp) }}</dd></div>
          <div><dt>策略／执行</dt><dd>{{ decisionLabels[selectedAudit.decision] || selectedAudit.decision }} / {{ executionLabels[selectedAudit.execution_status] || selectedAudit.execution_status }}</dd></div>
          <div><dt>请求编号</dt><dd><code>{{ selectedAudit.request_id }}</code></dd></div>
          <div><dt>用户编号</dt><dd><code>{{ selectedAudit.actor_id || '系统事件' }}</code></dd></div>
          <div><dt>策略编号</dt><dd>{{ selectedAudit.policy_id || '未记录' }}</dd></div>
          <div><dt>记录耗时</dt><dd>{{ selectedAudit.duration_ms.toFixed(2) }} ms</dd></div>
        </dl>
        <section v-if="selectedAudit.details?.resource_name" class="audit-detail-block"><h3>目标名称</h3><p>{{ selectedAudit.details.resource_name }}</p></section>
        <section v-if="selectedAudit.details?.resource_ids?.length" class="audit-detail-block"><h3>目标资源编号</h3><ul><li v-for="id in selectedAudit.details.resource_ids" :key="id"><code>{{ id }}</code></li></ul></section>
        <section v-if="selectedAudit.details?.question" class="audit-detail-block"><h3>提交的问题／查询内容</h3><p>{{ selectedAudit.details.question }}</p></section>
        <section v-if="selectedAudit.details?.sql" class="audit-detail-block"><h3>已校验的参数化 SQL</h3><pre>{{ selectedAudit.details.sql }}</pre><small>项目参数由服务端绑定。记录了 SQL 不代表执行成功，请结合执行状态和同一请求编号判断。</small></section>
        <p v-if="!hasAuditDetails(selectedAudit)" class="audit-detail-empty">此记录未保存操作详情；历史记录不反推查询内容或目标名称。</p>
        <p class="audit-detail-note">问题和名称经过敏感标记检查；不展示论文正文、结果行或被拒绝的原始候选 SQL。同一请求的业务与 SQL 事件通过请求编号关联。</p>
      </template>
    </el-dialog>
  </div>
</template>
