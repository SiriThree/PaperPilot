<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { api, type Models } from '../api/client'
import { formatError } from '../api/errors'
import { useResource } from '../composables/useResource'
import { experimentCategories, experimentConfigurations, experimentConfigurationLabels, validateExperiment, type ExperimentReport, type ExperimentRecord, type ExperimentConfig } from '../securityExperiment'
import AppIcon from './AppIcon.vue'
import Feedback from './Feedback.vue'

const reportUrl = '/security-experiment.json'
const props = defineProps<{ projectId: string; owner: boolean }>()
const resource = useResource<ExperimentReport | null>(null, async () => {
  const response = await fetch(reportUrl, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!response.ok) throw new Error('Unavailable experiment')
  return validateExperiment(await response.json())
})
const live = ref<Models['SecurityExperimentState'] | null>(null), starting = ref(false), liveAttempted = ref(false), liveError = ref('')
const liveReport = shallowRef<ExperimentReport | null>(null)
const report = computed(() => liveAttempted.value ? liveReport.value : resource.data.value)
const category = ref('cross_project'), caseId = ref('cross_project-01')
const running = computed(() => starting.value || live.value?.busy === true)
const phaseLabels: Record<string, string> = { starting: '准备 Docker 和隔离环境', no_gateway: '阶段 1/3：执行无网关基线', gateway_only: '阶段 2/3：执行仅应用网关配置', gateway_and_database: '阶段 3/3：执行网关与数据库权限配置', finished: '实验运行结束' }
const failureLabels: Record<string, string> = { DOCKER_UNAVAILABLE: 'Docker 不可用，请启动 Docker Desktop 的 Linux 引擎后重试。', DOCKER_IMAGE_MISSING: '缺少固定实验镜像，请按演示指南准备镜像后重试。', DOCKER_CONTAINER_FAILED: 'Docker 临时容器启动或运行失败，请检查镜像、端口和资源。', EXPERIMENT_FAILED: '实验未能完成，没有本次结果。请检查本机环境后重试。', EXPERIMENT_TIMEOUT: '实验超时，已停止本次任务并清理专属容器。', EXPERIMENT_INVALID_RESULT: '本次结果不完整或来源不匹配，不能展示通过结论。', EXPERIMENT_CANCELLED: '服务关闭，本次实验已停止。', EXPERIMENT_CLEANUP_FAILED: '实验容器清理未确认，暂不展示本次结果。' }
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, sequence = 0
function acceptState(value: Models['SecurityExperimentState']) {
  if (disposed || !props.owner) return
  live.value = value
  if (value.run_id) { liveAttempted.value = true; liveReport.value = null }
  if (value.status === 'succeeded' && !value.busy) {
    const measured = validateExperiment(value.report as ExperimentReport)
    if (!value.run_id || value.report?.run_id !== value.run_id || !value.started_at || Date.parse(measured.generated_at) < Date.parse(value.started_at)) throw new Error('Invalid live experiment result')
    liveReport.value = measured
  } else if (value.status === 'failed') {
    liveReport.value = null
    liveError.value = `${failureLabels[value.error || ''] || failureLabels.EXPERIMENT_FAILED}（${value.error || 'EXPERIMENT_FAILED'}）`
  }
  if (value.busy) timer = setTimeout(poll, 1000)
}
async function poll() {
  if (disposed || !props.owner) return
  clearTimeout(timer)
  const current = ++sequence
  liveError.value = ''
  try { const value = await api.securityExperiment(props.projectId); if (current === sequence) acceptState(value) }
  catch (failure) { if (!disposed && current === sequence) liveError.value = formatError(failure) }
}
async function startExperiment() {
  if (running.value || !props.owner) return
  clearTimeout(timer)
  const current = ++sequence
  liveAttempted.value = true; liveReport.value = null; liveError.value = ''; starting.value = true; live.value = null
  try { const value = await api.startSecurityExperiment(props.projectId); if (current === sequence) acceptState(value) }
  catch (failure) { if (!disposed && current === sequence) liveError.value = formatError(failure) }
  finally { if (!disposed) starting.value = false }
}
function history() { if (!running.value) { liveAttempted.value = false; liveError.value = ''; resource.load() } }
function downloadReport() {
  if (!report.value) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(report.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = `security-experiment-${liveAttempted.value ? live.value?.run_id : 'history'}.json`; link.click(); URL.revokeObjectURL(url)
}
onMounted(resource.load)
watch(() => props.owner, owner => {
  if (owner) void poll()
  else { sequence++; clearTimeout(timer); live.value = null; liveReport.value = null; liveAttempted.value = false }
}, { immediate: true })
onScopeDispose(() => { disposed = true; clearTimeout(timer) })
const cases = computed(() => report.value ? [...report.value.dataset.attacks, ...report.value.dataset.normal.map(c => ({ ...c, category: 'normal' }))] : [])
const filteredCases = computed(() => cases.value.filter(c => c.category === category.value))
const selectedCase = computed(() => cases.value.find(c => c.id === caseId.value))
watch(filteredCases, items => { if (!items.some(c => c.id === caseId.value)) caseId.value = items[0]?.id || '' })
const configurations = computed(() => experimentConfigurations.map(id => report.value?.configurations.find(c => c.configuration === id)).filter(c => !!c))
const full = computed(() => configurations.value.find(c => c.configuration === 'gateway_and_database'))
const maximumP95 = computed(() => Math.max(...(full.value?.performance.map(p => p.gateway_added_p95_ms) || [0])))
const probeId = ref('foreign_view')
const relatedProbe = computed(() => {
  if (['cross_project', 'prompt_injection'].includes(category.value)) return 'foreign_view'
  if (/^(UPDATE|DELETE)\b/i.test(selectedCase.value?.sql || '')) return 'write'
  if (category.value === 'sql_injection') return 'base_table'
  return null
})
watch(selectedCase, () => { if (relatedProbe.value) probeId.value = relatedProbe.value })
const blockedAtGateway = computed(() => ['gateway_only', 'gateway_and_database'].every(id => {
  const record = selectedRecord(id)
  return record?.decision === 'rejected' && record.execution_status === 'not_executed'
}))
function pick(type: string, id: string) { category.value = type; caseId.value = id; if (relatedProbe.value) probeId.value = relatedProbe.value }
function selectedRecord(id: string) { return configurations.value.find(c => c.configuration === id)?.records.find(r => r.case === caseId.value) }
function selectedProbe(config: ExperimentConfig) { return config.direct_bypass.find(p => p.case === probeId.value)! }
function queryPath(id: string, record: ExperimentRecord) {
  const rejected = record.decision === 'rejected' && record.execution_status === 'not_executed'
  return [
    { label: '候选 SQL', status: '已生成', state: 'done' },
    { label: '应用网关', status: id === 'no_gateway' ? '主动绕过' : rejected ? '已拒绝' : record.decision === 'allowed' ? '已通过' : '未确认', state: id === 'no_gateway' ? 'bypassed' : rejected ? 'blocked' : record.decision === 'allowed' ? 'done' : 'unknown' },
    { label: '数据库查询', status: rejected ? '未到达' : record.execution_status === 'executed' ? id === 'gateway_and_database' ? '已执行（受限身份）' : '已执行（宽权限）' : '未确认执行完成', state: rejected ? 'skipped' : record.execution_status === 'executed' ? 'done' : 'unknown' },
  ]
}
function probeOutcome(config: ExperimentConfig) {
  const probe = selectedProbe(config)
  return probe.attack_success ? '绕过成功' : probe.reason === '42501' ? '数据库权限拒绝' : probe.reason === 'DATABASE_SCOPE_FILTERED' ? '数据库按项目过滤' : '直接访问未成功'
}
function outcome(record: ExperimentRecord) { return record.category === 'normal' ? record.normal_passed ? '正常结果一致' : '正常查询未通过' : record.attack_success ? '越界候选已执行' : record.execution_status === 'not_executed' ? '策略拒绝，未执行' : '执行未完成' }
const reasons: Record<string, string> = { SQL_POLICY_REJECTED: 'AST 不符合三类统计允许清单', UNSUPPORTED_QUERY: '问题不在支持范围', UNSUPPORTED_SQL_EXECUTED: '此基线绕过网关，越界候选已执行', NORMAL_RESULT_MATCH: '结果与独立统计参考一致', NORMAL_RESULT_MISMATCH: '结果与参考不一致', DATABASE_SCOPE_FILTERED: '项目视图过滤，外项目返回零行', BYPASS_EXECUTED: '绕过网关后数据库仍允许此操作', '42501': '数据库原生权限拒绝' }
const probes: Record<string, string> = { foreign_view: '直接读取外项目视图', base_table: '直接读取业务底表', write: '直接修改业务数据' }
const mechanisms = { no_gateway: '故意放宽的隔离基线：绕过应用授权和 SQL 策略，操作逐次回滚。', gateway_only: '应用层重新核对项目权限，执行 AST 允许清单、可信参数绑定和关键审计。', gateway_and_database: '保留应用网关，再以项目独立只读身份、安全屏障视图和原生 ACL 约束执行。' }
const percentage = (value: number) => `${(value * 100).toFixed(1)}%`
</script>

<template>
  <section id="security-experiment" class="work-panel security-experiment" aria-label="攻防实验演示">
    <div class="work-panel-heading"><span class="work-icon"><AppIcon name="shield" /></span><div><h2>攻防实验</h2><p>{{ liveAttempted ? '实时运行 · 三种防护配置对照' : '历史结果回放 · 可从下方启动实时实验' }}</p></div><span class="provenance-badge">独立临时库，业务项目不受攻击</span></div>
    <div class="experiment-live-controls">
      <p>点击后实际执行 40 个攻击、21 个正常查询、数据库直接访问和性能测量，完成后展示此次运行结果。提示词仍使用受控候选 SQL，不调用真实模型。</p>
      <el-button type="primary" :loading="running" :disabled="!owner || !live?.enabled || running" @click="startExperiment">{{ running ? '实验运行中' : '开始实时实验' }}</el-button>
      <el-button v-if="owner" :disabled="starting" @click="poll">查询运行进度</el-button>
      <el-button :disabled="running" @click="history">查看历史报告</el-button>
      <p v-if="!owner">仅项目负责人可启动和查看实时实验。</p>
      <p v-else-if="live?.enabled === false">本服务未启用实时实验。请用 scripts/preview_integration.py 启动本机演示环境。</p>
      <p v-if="running" role="status">{{ phaseLabels[live?.phase || 'starting'] }}。请保留任务编号；刷新页面后可继续查询，不会重复启动。</p>
      <p v-if="live?.busy && !live.run_id">本机另一个隔离实验正在运行，结束后才能启动新实验。</p>
      <p v-if="liveAttempted && live?.run_id">任务编号 <code>{{ live.run_id }}</code> · 开始 {{ live.started_at ? new Date(live.started_at).toLocaleString('zh-CN') : '待确认' }}{{ live.finished_at ? ` · 完成 ${new Date(live.finished_at).toLocaleString('zh-CN')}` : '' }}</p>
      <p v-if="liveError" class="experiment-live-error" role="alert">{{ liveError }} 若启动响应未确认，请先查询运行进度。</p>
      <p v-if="liveAttempted && !report && !running">尚无可展示的本次实时结果；历史通过记录不会作为本次结果展示。</p>
    </div>
    <Feedback v-if="!liveAttempted" :busy="resource.loading.value" :error="resource.error.value ? '实验报告不可用或记录不完整，不能确认实验通过。' : ''" retry-label="重新读取实验报告" @retry="resource.load" />
    <template v-if="report && (liveAttempted || !resource.error.value)">
      <div class="experiment-verdict" :class="report.accepted ? 'experiment-pass' : 'experiment-fail'" role="status"><strong>{{ report.accepted ? '完整配置：本次固定实验达到目标' : '本次实验未达到全部验收目标' }}</strong><span>{{ report.dataset.attacks.length }} 个攻击 / {{ report.dataset.normal.length }} 个正常用例 · 完整配置网关新增 P95 最高 {{ maximumP95.toFixed(2) }} ms</span></div>
      <p class="experiment-boundary">攻击率按“越界候选被执行”计，不等于每条都泄露了数据。提示词组使用受控恶意模型输出，不是实际模型抗注入评分。当前业务查询不会切换到不安全基线。</p>
      <div class="experiment-presets" aria-label="攻防演示案例"><button @click="pick('cross_project', 'cross_project-01')">演示跨项目读取</button><button @click="pick('dangerous_sql', 'dangerous_sql-01')">演示危险写入</button><button @click="pick('prompt_injection', 'prompt-06')">演示论文提示注入</button><button @click="pick('normal', 'normal-1-1')">演示正常查询</button></div>
      <div class="experiment-selectors"><div><label class="field-label" for="experiment-category">用例类型</label><select id="experiment-category" v-model="category"><option v-for="(label, value) in experimentCategories" :key="value" :value="value">{{ label }}</option></select></div><div><label class="field-label" for="experiment-case">固定用例</label><select id="experiment-case" v-model="caseId"><option v-for="item in filteredCases" :key="item.id" :value="item.id">{{ item.id }}{{ item.question ? ` · ${item.question}` : '' }}</option></select></div></div>
      <div v-if="selectedCase" class="experiment-candidate"><h3>{{ experimentCategories[category] }} · {{ selectedCase.id }}</h3><p v-if="selectedCase.prompt"><strong>{{ selectedCase.origin === 'paper' ? '不可信论文内容' : '不可信用户提示' }}：</strong>{{ selectedCase.prompt }}</p><pre aria-label="固定候选 SQL 模板">{{ selectedCase.sql }}</pre><small>固定模板中的项目编号为逻辑占位；每组实际 SQL 使用各自临时库的项目编号，可展开原始记录核对。</small></div>
      <h3 class="experiment-scenario-title">场景一：经过网关的查询入口</h3>
      <p v-if="blockedAtGateway" class="experiment-explanation">两组结果一致的原因：网关已拦截，数据库层未触发。下方场景二绕过网关，单独验证数据库权限的作用。</p>
      <div class="experiment-comparison">
        <article v-for="config in configurations" :key="config.configuration" :data-testid="`experiment-${config.configuration}`" class="experiment-config">
          <h3>{{ experimentConfigurationLabels[config.configuration] }}</h3><p class="experiment-mechanism">{{ mechanisms[config.configuration] }}</p>
          <dl class="experiment-rates"><div><dt>攻击候选执行率</dt><dd>{{ percentage(config.attack_success_rate) }}</dd></div><div><dt>正常结果通过率</dt><dd>{{ percentage(config.normal_pass_rate) }}</dd></div></dl>
          <template v-if="selectedRecord(config.configuration)">
            <ol class="experiment-path" aria-label="本例执行路径"><li v-for="step in queryPath(config.configuration, selectedRecord(config.configuration)!)" :key="step.label" :data-state="step.state"><strong>{{ step.label }}</strong><span>{{ step.status }}</span></li></ol>
            <div class="experiment-case-result" :class="selectedRecord(config.configuration)!.category === 'normal' ? selectedRecord(config.configuration)!.normal_passed ? 'experiment-pass' : 'experiment-fail' : selectedRecord(config.configuration)!.attack_success ? 'experiment-fail' : 'experiment-pass'"><strong>{{ outcome(selectedRecord(config.configuration)!) }}</strong><span>{{ selectedRecord(config.configuration)!.decision }} / {{ selectedRecord(config.configuration)!.execution_status }}</span><span>{{ selectedRecord(config.configuration)!.row_count }} 行 · {{ reasons[selectedRecord(config.configuration)!.reason] || selectedRecord(config.configuration)!.reason }}</span></div>
            <details class="experiment-record"><summary>查看本例原始结果与审计</summary><p>请求编号 <code>{{ selectedRecord(config.configuration)!.request_id }}</code></p><pre>{{ selectedRecord(config.configuration)!.candidate_sql }}</pre><p>原因代码：{{ selectedRecord(config.configuration)!.reason }}</p><ul v-if="selectedRecord(config.configuration)!.audit.length"><li v-for="event in selectedRecord(config.configuration)!.audit" :key="event.event_id"><code>{{ event.action }}</code> · {{ event.decision }} / {{ event.execution_status }}<small>事件编号 {{ event.event_id }}</small></li></ul><p v-else>未产生 D 网关审计事件。</p><small>摘录来自本次实验临时库；临时库结束后清理，不属于当前项目日志。</small></details>
          </template>
        </article>
      </div>
      <section class="experiment-probes" aria-label="绕过网关的数据库直接访问">
        <h3 class="experiment-scenario-title">场景二：绕过网关，直接访问数据库</h3>
        <p>应用网关只能检查经过它的请求。本场景直接使用各组实验数据库身份访问，单独验证第二层约束，不计入上方攻击集分母。</p>
        <p v-if="relatedProbe && probeId === relatedProbe" class="experiment-explanation">已按上方用例的攻击目标联动：{{ probes[probeId] }}。这里使用独立的代表 SQL，并非上方同一条 SQL 的重放。</p>
        <p v-else class="experiment-explanation">{{ category === 'normal' ? '正常查询无需绕过网关。' : '' }}以下为独立数据库探测，不是上方用例的执行结果。</p>
        <label class="field-label" for="experiment-probe">直接访问目标</label><select id="experiment-probe" v-model="probeId"><option v-for="(label, id) in probes" :key="id" :value="id">{{ label }}</option></select>
        <div class="experiment-comparison">
          <article v-for="config in configurations" :key="config.configuration" :data-testid="`probe-${config.configuration}`" class="experiment-config">
            <h3>{{ experimentConfigurationLabels[config.configuration] }}</h3>
            <ol class="experiment-path" aria-label="直接访问路径"><li data-state="done"><strong>探测 SQL</strong><span>独立代表用例</span></li><li data-state="bypassed"><strong>应用网关</strong><span>主动绕过</span></li><li :data-state="selectedProbe(config).attack_success ? 'bypassed' : ['42501', 'DATABASE_SCOPE_FILTERED'].includes(selectedProbe(config).reason) ? 'blocked' : 'unknown'"><strong>数据库</strong><span>{{ probeOutcome(config) }}</span></li></ol>
            <div class="experiment-case-result" :class="selectedProbe(config).attack_success ? 'experiment-fail' : ['42501', 'DATABASE_SCOPE_FILTERED'].includes(selectedProbe(config).reason) ? 'experiment-pass' : ''"><strong>{{ probeOutcome(config) }}</strong><span>{{ selectedProbe(config).row_count }} 行 · {{ reasons[selectedProbe(config).reason] || selectedProbe(config).reason }}</span><span>原因代码：{{ selectedProbe(config).reason }}</span></div>
            <details class="experiment-record"><summary>查看直接访问的实际 SQL</summary><pre>{{ selectedProbe(config).candidate_sql }}</pre><p>本组直接使用{{ config.configuration === 'gateway_and_database' ? '项目独立受限身份' : '基线宽权限身份' }}。写操作在隔离实验中回滚。</p><small>此探测不经过网关；报告未提供请求编号和网关审计，不补造相关记录。</small></details>
          </article>
        </div>
      </section>
      <details class="experiment-method"><summary>测量口径、来源与现场复跑</summary><p>串行、小规模合成数据；每类先预热 5 次，再测至少 30 次。新增耗时排除整个执行器调用，包含应用授权、AST 和关键审计 I/O，不能当作完整 HTTP 延迟。</p><div class="experiment-performance" v-if="full"><div v-for="item in full.performance" :key="item.question"><strong>{{ item.question }}</strong><span>{{ item.samples.length }} 次测量</span><span>新增 P95 {{ item.gateway_added_p95_ms.toFixed(2) }} ms / 目标 ≤200 ms</span><span>完整适配器 P95 {{ item.total_p95_ms.toFixed(2) }} ms</span></div></div><p>{{ report.platform }} · {{ report.logical_cpus }} 逻辑 CPU · {{ report.physical_memory_gib }} GiB · PostgreSQL {{ full?.postgres_version }}</p><p>运行时间 {{ new Date(report.generated_at).toLocaleString('zh-CN') }} · 提交 {{ report.commit.slice(0, 7) }}{{ report.worktree_dirty ? '（含未提交改动）' : '' }}</p><p>应用指纹 <code>{{ report.application_fingerprint }}</code></p><p>用例摘要 <code>{{ report.dataset_sha256 }}</code></p><p>三组业务数据前后快照一致；不向大库或高并发外推结果。</p><p>上方按钮可直接启动实时实验。也可在终端运行以下命令生成历史报告；命令只创建自己的临时数据库。</p><pre>.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/evaluate_security.py --demo --output frontend/public/security-experiment.json</pre><el-button @click="downloadReport">下载当前结果 JSON</el-button></details>
    </template>
    <p v-else-if="!resource.loading.value && !running" class="experiment-boundary">未取得有效实验记录，暂不展示通过率或通过结论。</p>
  </section>
</template>
