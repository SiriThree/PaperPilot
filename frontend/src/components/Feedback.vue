<script setup lang="ts">
defineProps<{ busy?: boolean; error?: string; retryLabel?: string }>()
defineEmits<{ retry: [] }>()
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
  <el-button v-if="error && retryLabel" :disabled="busy" @click="$emit('retry')">{{ retryLabel }}</el-button>
  <p v-if="busy" class="muted" role="status">正在处理，请稍候…</p>
</template>
