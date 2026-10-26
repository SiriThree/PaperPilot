<script setup lang="ts">
import type { Models } from '../api/client'
defineProps<{ sources: Models['Source'][] }>()
</script>
<template>
  <div class="sources">
    <el-empty v-if="!sources.length" description="暂无可展示来源" :image-size="48" />
    <el-collapse v-else>
      <el-collapse-item v-for="source in sources" :key="source.chunk_id" :name="source.citation_id" :title="`[${source.citation_id}] 第 ${source.page_number} 页 · 合成来源，待核验`">
        <p>{{ source.excerpt }}</p>
        <small class="muted">论文 {{ source.paper_id }}<br>片段 {{ source.chunk_id }}</small>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>
