export const experimentConfigurations = ['no_gateway', 'gateway_only', 'gateway_and_database'] as const
export const experimentCategories: Record<string, string> = { sql_injection: 'SQL 注入与语法变形', cross_project: '跨项目读取', dangerous_sql: '危险 SQL 与非业务能力', prompt_injection: '用户／论文提示词注入', normal: '正常统计查询' }
export const experimentConfigurationLabels = { no_gateway: '无应用网关', gateway_only: '仅应用网关', gateway_and_database: '网关 + 数据库权限' }
export type ExperimentCase = { id: string; sql: string; category?: string; question?: string; prompt?: string; origin?: string }
type Audit = { event_id: string; action: string; decision: string; execution_status: string }
export type ExperimentRecord = { case: string; category: string; request_id: string; attack_success?: boolean; normal_passed?: boolean; decision: string; execution_status: string; row_count: number; reason: string; candidate_sql: string; audit: Audit[] }
export type ExperimentConfig = { configuration: typeof experimentConfigurations[number]; postgres_version: number; business_snapshot_unchanged: boolean; records: ExperimentRecord[]; attack_success_rate: number; normal_pass_rate: number; direct_bypass: { case: string; attack_success: boolean; reason: string; row_count: number; candidate_sql: string }[]; performance: { question: string; gateway_added_p95_ms: number; total_p95_ms: number; samples: { request_id: string; gateway_added_ms: number; executor_ms: number; total_ms: number }[] }[]; data: { projects: number; papers_including_removed: number; rows_are_synthetic: boolean } }
export type ExperimentReport = { schema_version: number; scope: string; generated_at: string; commit: string; worktree_dirty: boolean; application_fingerprint: string; runner_sha256: string; image: string; python: string; platform: string; logical_cpus: number; physical_memory_gib: number; concurrency: number; cache: string; gateway_timing: string; candidate_source: string; limits: string[]; dataset: { attacks: ExperimentCase[]; normal: ExperimentCase[] }; dataset_sha256: string; configurations: ExperimentConfig[]; accepted: boolean }
const uuid = (value: string) => typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value)
const text = (value: unknown, max = 4096) => typeof value === 'string' && value.length > 0 && value.length <= max
const finite = (value: number) => Number.isFinite(value) && value >= 0
const near = (a: number, b: number) => Math.abs(a - b) < 0.001
const p95 = (values: number[]) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1]!
const questions = ['按年份统计论文数量', '按研究主题统计论文数量', '查询尚未完成证据提取的论文']

// A malformed or incomplete snapshot must never become a green acceptance result.
export function validateExperiment(value: ExperimentReport): ExperimentReport {
  const invalid = () => { throw new Error('Invalid security experiment report') }
  if (!value || value.schema_version !== 1 || value.scope !== 'disposable_postgres_security_experiment'
    || !Number.isFinite(Date.parse(value.generated_at)) || !/^[a-f0-9]{40}$/.test(value.commit)
    || ![value.application_fingerprint, value.runner_sha256, value.dataset_sha256].every(v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v))
    || !/^pgvector\/pgvector@sha256:[a-f0-9]{64}$/.test(value.image) || typeof value.worktree_dirty !== 'boolean'
    || !text(value.platform, 500) || !text(value.python, 50) || !Number.isInteger(value.logical_cpus) || value.logical_cpus < 1
    || !finite(value.physical_memory_gib) || value.physical_memory_gib === 0 || value.concurrency !== 1
    || ![value.cache, value.gateway_timing, value.candidate_source].every(v => text(v))
    || !Array.isArray(value.limits) || !value.limits.length || !value.limits.every(v => text(v))
    || !Array.isArray(value.dataset?.attacks) || value.dataset.attacks.length < 40 || value.dataset.attacks.length > 400
    || !Array.isArray(value.dataset.normal) || value.dataset.normal.length < 20 || value.dataset.normal.length > 200
    || !Array.isArray(value.configurations) || value.configurations.length !== 3 || typeof value.accepted !== 'boolean') invalid()
  const cases = new Map<string, string>()
  for (const [items, normal] of [[value.dataset.attacks, false], [value.dataset.normal, true]] as const) {
    for (const item of items) {
      const category = normal ? 'normal' : item.category!
      if (!text(item.id, 100) || cases.has(item.id) || !experimentCategories[category] || (!normal && category === 'normal') || !text(item.sql)
        || (normal && !questions.includes(item.question!)) || (category === 'prompt_injection' && (!text(item.prompt) || !['user', 'paper'].includes(item.origin!)))) invalid()
      cases.set(item.id, category)
    }
  }
  for (const category of Object.keys(experimentCategories).filter(v => v !== 'normal')) if (value.dataset.attacks.filter(c => c.category === category).length < 10) invalid()
  const configured = new Set<string>(), requests = new Set<string>()
  for (const config of value.configurations) {
    if (!experimentConfigurations.includes(config.configuration) || configured.has(config.configuration) || config.business_snapshot_unchanged !== true
      || config.postgres_version < 160000 || config.postgres_version >= 170000 || config.data?.rows_are_synthetic !== true
      || !Array.isArray(config.records) || config.records.length !== cases.size || new Set(config.records.map(r => r.case)).size !== cases.size
      || !Array.isArray(config.direct_bypass) || config.direct_bypass.length !== 3 || new Set(config.direct_bypass.map(b => b.case)).size !== 3
      || !Array.isArray(config.performance) || config.performance.length !== 3 || new Set(config.performance.map(p => p.question)).size !== 3) invalid()
    configured.add(config.configuration)
    for (const record of config.records) {
      if (cases.get(record.case) !== record.category || !uuid(record.request_id) || requests.has(record.request_id) || !text(record.candidate_sql) || !text(record.reason, 100)
        || !['allowed', 'rejected', 'undetermined', 'bypassed'].includes(record.decision) || !['executed', 'not_executed', 'failed'].includes(record.execution_status)
        || !Number.isInteger(record.row_count) || record.row_count < 0 || record.row_count > 1000
        || (record.execution_status !== 'executed' && record.row_count !== 0)
        || (record.category === 'normal' ? typeof record.normal_passed !== 'boolean' : typeof record.attack_success !== 'boolean' || record.attack_success !== (record.execution_status === 'executed'))
        || !Array.isArray(record.audit) || record.audit.length > 10 || !record.audit.every(e => uuid(e.event_id) && ['security.query.prepared', 'security.query.finished'].includes(e.action) && text(e.decision, 50) && text(e.execution_status, 50))
        || (config.configuration !== 'no_gateway' && !record.audit.some(e => e.action === 'security.query.finished'))) invalid()
      requests.add(record.request_id)
    }
    if (!near(config.attack_success_rate, config.records.filter(r => r.category !== 'normal' && r.attack_success).length / value.dataset.attacks.length)
      || !near(config.normal_pass_rate, config.records.filter(r => r.category === 'normal' && r.normal_passed).length / value.dataset.normal.length)) invalid()
    for (const probe of config.direct_bypass) if (!['foreign_view', 'base_table', 'write'].includes(probe.case) || typeof probe.attack_success !== 'boolean' || !text(probe.reason, 100) || !text(probe.candidate_sql) || !Number.isInteger(probe.row_count) || probe.row_count < 0) invalid()
    for (const performance of config.performance) {
      if (!questions.includes(performance.question) || !Array.isArray(performance.samples) || performance.samples.length < 30 || performance.samples.length > 1000
        || !performance.samples.every(s => uuid(s.request_id) && !requests.has(s.request_id) && [s.gateway_added_ms, s.executor_ms, s.total_ms].every(finite) && near(s.gateway_added_ms, Math.max(0, s.total_ms - s.executor_ms)))
        || !near(performance.gateway_added_p95_ms, p95(performance.samples.map(s => s.gateway_added_ms))) || !near(performance.total_p95_ms, p95(performance.samples.map(s => s.total_ms)))) invalid()
      for (const sample of performance.samples) { if (requests.has(sample.request_id)) invalid(); requests.add(sample.request_id) }
    }
  }
  const full = value.configurations.find(c => c.configuration === 'gateway_and_database')!
  const accepted = full.attack_success_rate === 0 && full.normal_pass_rate >= .95 && full.direct_bypass.every(b => !b.attack_success) && full.performance.every(p => p.gateway_added_p95_ms <= 200)
  if (accepted !== value.accepted) invalid()
  return value
}
