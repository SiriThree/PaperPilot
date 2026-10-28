<script setup lang="ts">
import { onMounted } from 'vue'
import { useResource } from '../composables/useResource'
import Feedback from './Feedback.vue'
import AppIcon from './AppIcon.vue'
type Group = { id: string; label: string; status: 'passed' | 'failed' | 'not_verified'; passed: number; failed: number; skipped: number; selectors: string[] }
type Report = { schema_version: number; scope: string; environment: string; generated_at: string; commit: string; worktree_dirty: boolean; code_fingerprint: string; groups: Group[]; not_verified: string[] }
const labels = { passed: '应用层测试通过（Mock）', failed: '存在失败', not_verified: '未完成验证' }
const resource = useResource<Report | null>(null, async () => {
  const response = await fetch('/security-evidence.json', { cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!response.ok) throw new Error('Unavailable evidence')
  const value = await response.json()
  if (value.schema_version !== 1 || value.scope !== 'mock_application' || !/^[a-f0-9]{40}$/.test(value.commit)
    || !/^[a-f0-9]{64}$/.test(value.code_fingerprint) || typeof value.worktree_dirty !== 'boolean'
    || !Number.isFinite(Date.parse(value.generated_at)) || !Array.isArray(value.groups) || value.groups.length !== 3
    || !value.groups.every((group: Group) => ['anonymous', 'project_scope', 'gateway_boundary'].includes(group.id)
      && typeof group.label === 'string' && ['passed', 'failed', 'not_verified'].includes(group.status)
      && [group.passed, group.failed, group.skipped].every(count => Number.isInteger(count) && count >= 0)
      && (group.status !== 'passed' || (group.passed > 0 && group.failed === 0 && group.skipped === 0)))
    || new Set(value.groups.map((group: Group) => group.id)).size !== 3) throw new Error('Invalid evidence')
  return value as Report
})
onMounted(resource.load)
</script>
<template>
  <section class="work-panel security-evidence" aria-label="安全验证证据">
    <div class="work-panel-heading"><span class="work-icon"><AppIcon name="shield" /></span><div><h2>验证证据</h2><p>离线测试记录，不是当前服务的实时探测</p></div></div>
    <Feedback :busy="resource.loading.value" :error="resource.error.value ? '验证报告不可用，当前不能确认测试结果。' : ''" retry-label="重新读取验证报告" @retry="resource.load" />
    <template v-if="resource.data.value"><p class="evidence-environment">隔离 Mock 环境 · 应用层行为验证</p><ul class="security-checks"><li v-for="group in resource.data.value.groups" :key="group.id"><span>{{ group.label }}</span><strong :class="`check-${group.status}`">{{ labels[group.status] }}</strong><small>{{ group.passed }} 通过 / {{ group.failed }} 失败 / {{ group.skipped }} 跳过</small></li></ul><dl class="evidence-run"><div><dt>执行时间</dt><dd><time :datetime="resource.data.value.generated_at">{{ new Date(resource.data.value.generated_at).toLocaleString('zh-CN') }}</time></dd></div><div><dt>版本</dt><dd>{{ resource.data.value.commit.slice(0, 7) }}{{ resource.data.value.worktree_dirty ? ' · 含未提交修改' : '' }}</dd></div><div><dt>代码指纹</dt><dd>{{ resource.data.value.code_fingerprint.slice(0, 12) }}</dd></div></dl><a href="/security-evidence.json" target="_blank" rel="noopener" class="evidence-download">查看实际测试记录 <AppIcon name="arrow" :size="13" /></a></template>
    <p v-else-if="!resource.loading.value" class="evidence-unrun">未取得有效报告 · 不展示通过结论</p>
    <div class="unverified-capabilities"><span>真实 SQL AST 与受限执行<strong>待 D 接入验收</strong></span><span>真实数据库隔离与恢复<strong>待 A 接入验收</strong></span></div>
  </section>
</template>
