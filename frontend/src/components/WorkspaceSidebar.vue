<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { projects, projectsLoaded, projectsLoading, projectsError, user } from '../state'
import AppIcon from './AppIcon.vue'
withDefaults(defineProps<{ logoutBusy: boolean; collapsed?: boolean }>(), { collapsed: false })
defineEmits<{ navigate: []; logout: []; toggleCollapse: [] }>()
const route = useRoute()
const router = useRouter()
const pid = computed(() => String(route.params.projectId || ''))
function switchProject(value: string) {
  const section = route.path.split('/').pop()
  void router.push({ path: `/projects/${value}/${['papers', 'research', 'security'].includes(section || '') ? section : 'papers'}`, query: route.query.view === 'report' ? { view: 'report' } : {} })
}
const sections = [
  { path: 'papers', name: '文献', label: '文献库', icon: 'book' },
  { path: 'research', name: '研究', label: '研究工作台', icon: 'file' },
  { path: 'research?view=report', name: '报告', label: '报告', icon: 'save' },
  { path: 'security', name: '查询与审计', label: '查询与审计', icon: 'shield' },
]
</script>
<template>
  <div class="sidebar-inner" :class="{ 'is-collapsed': collapsed }">
    <div class="sidebar-brand-row">
    <RouterLink to="/" class="workspace-brand" aria-label="PaperPilot Secure 首页" @click="$emit('navigate')">
      <span class="workspace-logo"><AppIcon name="book" :size="22" /></span>
      <span>PaperPilot</span>
    </RouterLink>
      <button class="sidebar-toggle" :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'" :title="collapsed ? '展开侧边栏' : '收起侧边栏'" :aria-expanded="!collapsed" @click="$emit('toggleCollapse')"><AppIcon name="menu" :size="17" /></button>
    </div>
    <el-select v-if="pid && !collapsed" class="sidebar-project-switcher" :model-value="pid" aria-label="切换项目" @change="switchProject"><el-option v-for="project in projects" :key="project.project_id" :value="project.project_id" :label="project.name" /></el-select>
    <nav class="sidebar-navigation" aria-label="工作空间导航">
      <RouterLink to="/" class="sidebar-link" aria-label="项目总览" title="项目总览" :class="{ 'is-current': !pid }" @click="$emit('navigate')"><AppIcon name="grid" /><span>项目总览</span><span class="nav-count">{{ projectsLoaded && !projectsLoading && !projectsError ? projects.length : '—' }}</span></RouterLink>
    </nav>
    <nav class="sidebar-navigation" aria-label="项目导航">
      <template v-for="section in sections" :key="section.path">
        <RouterLink v-if="pid" :to="`/projects/${pid}/${section.path}`" :aria-label="section.name" :title="section.label" class="sidebar-link" :class="{ 'is-report-link': section.name === '报告', 'is-selected-section': section.name === '报告' ? route.query.view === 'report' && route.path.endsWith('/research') : section.path === 'research' ? route.path.endsWith('/research') && route.query.view !== 'report' : route.path.endsWith('/' + section.path) }" @click="$emit('navigate')"><AppIcon :name="section.icon" /><span>{{ section.label }}</span></RouterLink>
        <span v-else class="sidebar-link is-disabled"><AppIcon :name="section.icon" /><span>{{ section.label }}</span></span>
      </template>
    </nav>
    <template v-if="projects.length">
      <div class="sidebar-section-label">项目</div>
      <nav class="sidebar-projects" aria-label="项目快捷入口">
        <RouterLink v-for="project in projects.slice(0, 4)" :key="project.project_id" :to="`/projects/${project.project_id}/papers`" :title="project.name" @click="$emit('navigate')"><span class="project-dot" /><span>{{ project.name }}</span></RouterLink>
      </nav>
    </template>
    <div class="sidebar-bottom">
      <div class="sidebar-principle"><AppIcon name="quote" :size="18" /><p>从文献到结论<br><strong>让研究有迹可循。</strong></p></div>
      <div class="sidebar-account"><span class="account-avatar">{{ user?.display_name.slice(0, 1) }}</span><span class="account-name">{{ user?.display_name }}</span><el-button text :loading="logoutBusy" aria-label="退出" @click="$emit('logout')"><AppIcon name="logout" :size="18" /></el-button></div>
    </div>
  </div>
</template>
