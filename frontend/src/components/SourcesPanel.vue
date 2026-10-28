<script setup lang="ts">
import type { Models } from '../api/client'
import AppIcon from './AppIcon.vue'
defineProps<{ sources: Models['Source'][] }>()
</script>
<template>
  <div class="sources">
    <div v-if="sources.length" class="source-guide"><AppIcon name="quote" :size="18" /><span>{{ sources.length }} 条来源 · 展开片段，对照页码核验</span></div>
    <el-empty v-if="!sources.length" description="暂无可展示来源" :image-size="48" />
    <el-collapse v-else>
      <el-collapse-item v-for="source in sources" :key="`${source.chunk_id}:${source.citation_id}`" :name="source.citation_id" :title="`[${source.citation_id}] 第 ${source.page_number} 页 · ${source.synthetic ? '合成来源' : '原文来源'}，待核验`">
        <blockquote class="source-excerpt"><AppIcon name="quote" :size="20" /><p>{{ source.excerpt }}</p></blockquote>
        <dl class="source-identifiers"><div><dt>论文编号</dt><dd>{{ source.paper_id }}</dd></div><div><dt>片段编号</dt><dd>{{ source.chunk_id }}</dd></div><div><dt>定位页码</dt><dd>第 {{ source.page_number }} 页</dd></div></dl>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>
