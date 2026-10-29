<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { projects, projectsLoaded, projectsLoading, projectsError, user, passwordAuth } from '../state'
import AppIcon from './AppIcon.vue'
defineProps<{ logoutBusy: boolean }>()
defineEmits<{ navigate: []; logout: [] }>()
const route = useRoute()
const pid = computed(() => String(route.params.projectId || ''))
const sections = [
  { path: 'papers', name: '文献', caption: '导入与整理', icon: 'book' },
  { path: 'research', name: '研究', caption: '检索与证据', icon: 'search' },
  { path: 'security', name: '查询与审计', caption: '统计与追踪', icon: 'shield' },
]
</script>
<template>
  <div class="sidebar-inner">
    <RouterLink to="/" class="workspace-brand" aria-label="PaperPilot Secure 首页" @click="$emit('navigate')">
      <span class="workspace-logo"><AppIcon name="book" :size="22" /></span>
      <span>PaperPilot<small>SECURE · RESEARCH</small></span>
    </RouterLink>
    <div class="sidebar-section-label">工作空间</div>
    <nav class="sidebar-navigation" aria-label="工作空间导航">
      <RouterLink to="/" class="sidebar-link" :class="{ 'is-current': !pid }" @click="$emit('navigate')"><AppIcon name="grid" /><span>项目总览</span><span class="nav-count">{{ projectsLoaded && !projectsLoading && !projectsError ? projects.length : '—' }}</span></RouterLink>
    </nav>
    <div class="sidebar-section-label">项目工作台 <small v-if="!pid">选择项目后使用</small></div>
    <nav class="sidebar-navigation" aria-label="项目导航">
      <template v-for="section in sections" :key="section.path">
        <RouterLink v-if="pid" :to="`/projects/${pid}/${section.path}`" :aria-label="section.name" class="sidebar-link" @click="$emit('navigate')"><AppIcon :name="section.icon" /><span>{{ section.name }}<small>{{ section.caption }}</small></span></RouterLink>
        <span v-else class="sidebar-link is-disabled"><AppIcon :name="section.icon" /><span>{{ section.name }}<small>{{ section.caption }}</small></span></span>
      </template>
    </nav>
    <template v-if="projects.length">
      <div class="sidebar-section-label">项目快捷入口</div>
      <nav class="sidebar-projects" aria-label="项目快捷入口">
        <RouterLink v-for="project in projects.slice(0, 4)" :key="project.project_id" :to="`/projects/${project.project_id}/papers`" :title="project.name" @click="$emit('navigate')"><span class="project-dot" /><span>{{ project.name }}</span></RouterLink>
      </nav>
    </template>
    <div class="sidebar-bottom">
      <div class="sidebar-principle"><AppIcon name="quote" :size="18" /><p>从文献到结论<br><strong>让研究有迹可循。</strong></p></div>
      <div class="sidebar-account"><span class="account-avatar">{{ user?.display_name.slice(0, 1) }}</span><span class="account-name">{{ user?.display_name }}<small>{{ passwordAuth ? '个人账户' : '开发演示身份' }}</small></span><el-button text :loading="logoutBusy" aria-label="退出" @click="$emit('logout')"><AppIcon name="logout" :size="18" /></el-button></div>
    </div>
  </div>
</template>
