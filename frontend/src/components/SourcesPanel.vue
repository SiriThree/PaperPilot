<script setup lang="ts">
import type { Models } from '../api/client'
import AppIcon from './AppIcon.vue'
defineProps<{ sources: Models['Source'][]; locatable?: boolean }>()
defineEmits<{ locate: [source: Models['Source']] }>()
</script>
<template>
  <div class="sources">
    <el-empty v-if="!sources.length" description="暂无可展示来源" :image-size="48" />
    <el-collapse v-else>
      <el-collapse-item v-for="source in sources" :key="`${source.chunk_id}:${source.citation_id}`" :name="source.citation_id" :title="`[${source.citation_id}] 第 ${source.page_number} 页 · ${source.synthetic ? '合成来源' : '原文来源'}，待核验`">
        <blockquote class="source-excerpt"><AppIcon name="quote" :size="20" /><p>{{ source.excerpt }}</p></blockquote>
        <el-button v-if="locatable" text type="primary" @click="$emit('locate', source)">定位来源</el-button>
        <details class="source-technical"><summary>来源标识</summary><dl class="source-identifiers"><div><dt>论文编号</dt><dd>{{ source.paper_id }}</dd></div><div><dt>片段编号</dt><dd>{{ source.chunk_id }}</dd></div></dl></details>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>
