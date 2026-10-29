<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useResource } from '../composables/useResource'
import Feedback from './Feedback.vue'
import AppIcon from './AppIcon.vue'
type Group = { id: string; label: string; status: 'passed' | 'failed' | 'not_verified'; passed: number; failed: number; skipped: number; selectors: string[] }
type Report = { schema_version: number; scope: string; environment: string; generated_at: string; commit: string; worktree_dirty: boolean; code_fingerprint: string; groups: Group[]; not_verified: string[]; source?: { verified: boolean; checkout: string; postgres_versions: string[]; image: string } }
const props = defineProps<{ real?: boolean }>()
const reportUrl = computed(() => props.real ? '/security-evidence-postgres.json' : '/security-evidence.json')
const labels = computed(() => ({ passed: props.real ? '隔离 PostgreSQL 测试通过' : '应用层测试通过（Mock）', failed: '存在失败', not_verified: '未完成验证' }))
const resource = useResource<Report | null>(null, async () => {
  const real = props.real
  const response = await fetch(reportUrl.value, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!response.ok) throw new Error('Unavailable evidence')
  const value = await response.json()
  const ids = real ? ['sql_execution', 'database_scope', 'audit_identity', 'recovery_integration'] : ['anonymous', 'project_scope', 'gateway_boundary']
  if (value.schema_version !== (real ? 2 : 1) || value.scope !== (real ? 'postgres_security' : 'mock_application') || !/^[a-f0-9]{40}$/.test(value.commit)
    || !/^[a-f0-9]{64}$/.test(value.code_fingerprint) || typeof value.worktree_dirty !== 'boolean'
    || typeof value.environment !== 'string' || value.environment.length > 300
    || !Number.isFinite(Date.parse(value.generated_at)) || !Array.isArray(value.groups) || value.groups.length !== ids.length
    || !value.groups.every((group: Group) => ids.includes(group.id)
      && typeof group.label === 'string' && group.label.length <= 200 && ['passed', 'failed', 'not_verified'].includes(group.status)
      && [group.passed, group.failed, group.skipped].every(count => Number.isInteger(count) && count >= 0 && count <= 1000000)
      && (group.status !== 'passed' || (group.passed > 0 && group.failed === 0 && group.skipped === 0)))
    || new Set(value.groups.map((group: Group) => group.id)).size !== ids.length
    || !Array.isArray(value.not_verified) || value.not_verified.length > 10 || !value.not_verified.every((item: unknown) => typeof item === 'string' && item.length <= 300)) throw new Error('Invalid evidence')
  if (real && (!value.verification_totals || ![value.verification_totals.passed, value.verification_totals.failed, value.verification_totals.skipped].every(count => Number.isInteger(count) && count >= 0 && count <= 1000000)
    || (value.verification_totals.failed > 0 && value.groups.some((g: Group) => g.status === 'passed'))
    || typeof value.source?.verified !== 'boolean'
    || (value.groups.some((g: Group) => g.status === 'passed') && (!value.source.verified
      || value.source.checkout !== 'current_checkout' || !/^pgvector\/pgvector@sha256:[a-f0-9]{64}$/.test(value.source.image)
      || !Array.isArray(value.source.postgres_versions) || !value.source.postgres_versions.length
      || !value.source.postgres_versions.every((v: string) => /^16\d{4}$/.test(v))))
    || value.groups.some((g: Group) => g.id === 'sql_execution' && g.status === 'passed' && g.passed < 109))) throw new Error('Invalid PostgreSQL evidence')
  return value as Report
})
onMounted(resource.load)
watch(() => props.real, () => { resource.data.value = null; resource.load() })
</script>
<template>
  <section class="work-panel security-evidence" aria-label="安全验证证据">
    <div class="work-panel-heading"><span class="work-icon"><AppIcon name="shield" /></span><div><h2>验证证据</h2><p>离线测试记录，不是当前服务的实时探测</p></div></div>
    <Feedback :busy="resource.loading.value" :error="resource.error.value ? '验证报告不可用，当前不能确认测试结果。' : ''" retry-label="重新读取验证报告" @retry="resource.load" />
    <template v-if="resource.data.value && !resource.error.value"><p class="evidence-environment">{{ real ? resource.data.value.environment : '隔离 Mock 环境 · 应用层行为验证' }}</p><ul class="security-checks"><li v-for="group in resource.data.value.groups" :key="group.id"><span>{{ group.label }}</span><strong :class="`check-${group.status}`">{{ labels[group.status] }}</strong><small>{{ group.passed }} 通过 / {{ group.failed }} 失败 / {{ group.skipped }} 跳过</small></li></ul><dl class="evidence-run"><div><dt>执行时间</dt><dd><time :datetime="resource.data.value.generated_at">{{ new Date(resource.data.value.generated_at).toLocaleString('zh-CN') }}</time></dd></div><div><dt>版本</dt><dd>{{ resource.data.value.commit.slice(0, 7) }}{{ resource.data.value.worktree_dirty ? ' · 含未提交修改' : '' }}</dd></div><div><dt>代码指纹</dt><dd>{{ resource.data.value.code_fingerprint.slice(0, 12) }}</dd></div></dl><a :href="reportUrl" target="_blank" rel="noopener" class="evidence-download">查看实际测试记录 <AppIcon name="arrow" :size="13" /></a></template>
    <p v-else-if="!resource.loading.value" class="evidence-unrun">未取得有效报告 · 不展示通过结论</p>
    <div v-if="real" class="unverified-capabilities"><span v-for="item in resource.data.value?.not_verified || ['未取得有效 PostgreSQL 报告，不能确认真实安全测试结果']" :key="item">{{ item }}</span></div>
    <div v-else class="unverified-capabilities"><span>真实 SQL AST 与受限执行<strong>待 D 接入验收</strong></span><span>真实数据库隔离与恢复<strong>待 A 接入验收</strong></span></div>
  </section>
</template>
