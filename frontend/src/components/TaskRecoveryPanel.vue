<script setup lang="ts">
import type { Models } from '../api/client'
import type { RecoveryRecord } from '../composables/useTaskRecovery'
defineProps<{ records: RecoveryRecord[]; selected: string; task: Models['Task'] | null; busy: boolean; message: string; storageError: string }>()
defineEmits<{ query: [id?: string]; reconcile: []; retry: [] }>()
const kinds = { process: '文献处理', answer: '问答', evidence: '证据提取', draft: '报告草稿' }
const statuses = { pending: '等待处理', processing: '处理中', succeeded: '已完成', failed: '服务端处理失败' }
</script>
<template>
  <section v-if="records.length || storageError" class="panel task-recovery" aria-label="任务恢复">
    <h2>任务恢复</h2>
    <el-alert v-if="storageError" :title="storageError" type="error" :closable="false" />
    <div class="toolbar"><el-button v-for="item in records" :key="item.id" :disabled="busy" :type="selected === item.id ? 'primary' : 'default'" size="small" @click="$emit('query', item.id)">{{ kinds[item.kind] }} · {{ item.taskId?.slice(0, 8) || '待核对' }}</el-button></div>
    <p v-if="task" role="status">任务：{{ task.status }} · {{ statuses[task.status] }}</p>
    <p v-if="records.find(item => item.id === selected)?.taskId" data-testid="recovery-task-id">{{ records.find(item => item.id === selected)?.taskId }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <el-button v-if="records.find(item => item.id === selected)?.taskId" :disabled="busy" @click="$emit('query')">重新查询任务</el-button>
    <el-button v-if="records.find(item => item.id === selected)?.phase === 'unknown'" :disabled="busy || !!storageError" @click="$emit('reconcile')">使用原请求核对</el-button>
    <el-button v-if="task?.status === 'failed'" :disabled="busy" @click="$emit('retry')">重试失败任务</el-button>
  </section>
</template>
