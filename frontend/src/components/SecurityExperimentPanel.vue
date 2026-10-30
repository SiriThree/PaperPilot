<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useResource } from '../composables/useResource'
import { experimentCategories, experimentConfigurations, experimentConfigurationLabels, validateExperiment, type ExperimentReport, type ExperimentRecord, type ExperimentConfig } from '../securityExperiment'
import AppIcon from './AppIcon.vue'
import Feedback from './Feedback.vue'

const reportUrl = '/security-experiment.json'
const resource = useResource<ExperimentReport | null>(null, async () => {
  const response = await fetch(reportUrl, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!response.ok) throw new Error('Unavailable experiment')
  return validateExperiment(await response.json())
})
onMounted(resource.load)
const report = resource.data, category = ref('cross_project'), caseId = ref('cross_project-01')
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
  <section id="security-experiment" class="work-panel security-experiment" aria-label="攻防实验历史回放">
    <div class="work-panel-heading"><span class="work-icon"><AppIcon name="shield" /></span><div><h2>攻防实验</h2><p>历史结果回放 · 同一固定用例，对比三种防护配置</p></div><span class="provenance-badge">隔离库实测，非当前项目的在线攻击</span></div>
    <Feedback :busy="resource.loading.value" :error="resource.error.value ? '实验报告不可用或记录不完整，不能确认实验通过。' : ''" retry-label="重新读取实验报告" @retry="resource.load" />
    <template v-if="report && !resource.error.value">
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
      <details class="experiment-method"><summary>测量口径、来源与现场复跑</summary><p>串行、小规模合成数据；每类先预热 5 次，再测至少 30 次。新增耗时排除整个执行器调用，包含应用授权、AST 和关键审计 I/O，不能当作完整 HTTP 延迟。</p><div class="experiment-performance" v-if="full"><div v-for="item in full.performance" :key="item.question"><strong>{{ item.question }}</strong><span>{{ item.samples.length }} 次测量</span><span>新增 P95 {{ item.gateway_added_p95_ms.toFixed(2) }} ms / 目标 ≤200 ms</span><span>完整适配器 P95 {{ item.total_p95_ms.toFixed(2) }} ms</span></div></div><p>{{ report.platform }} · {{ report.logical_cpus }} 逻辑 CPU · {{ report.physical_memory_gib }} GiB · PostgreSQL {{ full?.postgres_version }}</p><p>运行时间 {{ new Date(report.generated_at).toLocaleString('zh-CN') }} · 提交 {{ report.commit.slice(0, 7) }}{{ report.worktree_dirty ? '（含未提交改动）' : '' }}</p><p>应用指纹 <code>{{ report.application_fingerprint }}</code></p><p>用例摘要 <code>{{ report.dataset_sha256 }}</code></p><p>三组业务数据前后快照一致；不向大库或高并发外推结果。</p><p>现场复跑以下命令后，点击“重新读取实验报告”。命令只创建自己的临时数据库，不接收业务数据库凭据。</p><pre>.\.runtime\venv312\Scripts\python.exe -X utf8 scripts/evaluate_security.py --demo --output frontend/public/security-experiment.json</pre><el-button @click="resource.load" :disabled="resource.loading.value">重新读取实验报告</el-button><a :href="reportUrl" target="_blank" rel="noopener">查看全部原始结果 JSON</a></details>
    </template>
    <p v-else-if="!resource.loading.value" class="experiment-boundary">未取得有效实验记录，暂不展示通过率或通过结论。</p>
  </section>
</template>
