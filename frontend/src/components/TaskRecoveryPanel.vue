<script setup lang="ts">
import type { Models } from '../api/client'
import type { RecoveryRecord } from '../composables/useTaskRecovery'
import AppIcon from './AppIcon.vue'
defineProps<{ records: RecoveryRecord[]; selected: string; task: Models['Task'] | null; busy: boolean; message: string; storageError: string }>()
defineEmits<{ query: [id?: string]; reconcile: []; retry: [] }>()
const kinds = { process: '文献处理', answer: '问答', evidence: '证据提取', draft: '报告草稿' }
const statuses = { pending: '等待处理', processing: '处理中', succeeded: '已完成', failed: '服务端处理失败' }
</script>
<template>
  <section v-if="records.length || storageError" class="task-recovery work-panel" aria-label="任务恢复" :class="`task-state-${task?.status || 'unknown'}`">
    <div class="recovery-heading"><span class="recovery-icon"><AppIcon :name="task?.status === 'succeeded' ? 'check' : 'clock'" :size="18" /></span><h2>任务恢复</h2><span class="recovery-caption">查询已有进度，保留原任务</span><span v-if="task" class="recovery-status" role="status">任务：{{ task.status }} · {{ statuses[task.status] }}</span></div>
    <el-alert v-if="storageError" :title="storageError" type="error" :closable="false" />
    <div class="recovery-records"><el-button v-for="item in records" :key="item.id" :disabled="busy" :type="selected === item.id ? 'primary' : 'default'" size="small" @click="$emit('query', item.id)">{{ kinds[item.kind] }} · {{ item.taskId?.slice(0, 8) || '待核对' }}</el-button></div>
    <div class="recovery-current"><div class="recovery-text"><p v-if="message" role="status">{{ message }}</p><code v-if="records.find(item => item.id === selected)?.taskId" data-testid="recovery-task-id">{{ records.find(item => item.id === selected)?.taskId }}</code></div><div class="recovery-actions"><el-button v-if="records.find(item => item.id === selected)?.taskId" size="small" :disabled="busy" @click="$emit('query')"><AppIcon name="refresh" :size="14" />重新查询任务</el-button><el-button v-if="records.find(item => item.id === selected)?.phase === 'unknown'" size="small" :disabled="busy || !!storageError" @click="$emit('reconcile')">使用原请求核对</el-button><el-button v-if="task?.status === 'failed'" size="small" :disabled="busy" @click="$emit('retry')">重试失败任务</el-button></div></div>
  </section>
</template>
