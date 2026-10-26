<script setup lang="ts">
defineProps<{ busy?: boolean; error?: string }>()
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
  <p v-if="busy" class="muted" role="status">正在处理，请稍候…</p>
</template>
