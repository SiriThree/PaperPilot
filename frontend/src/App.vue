<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, onUnauthorized, sessionChanged } from './api/client'
import { formatError } from './api/errors'
import { booting, health, intendedRoute, persistentData, projects, refreshProjects, setIdentity, user } from './state'
import { useAction } from './composables/useAction'
import Feedback from './components/Feedback.vue'
import AppIcon from './components/AppIcon.vue'
import WorkspaceSidebar from './components/WorkspaceSidebar.vue'
const route = useRoute(), router = useRouter(), startupError = ref(''), connecting = ref(false), sessionNotice = ref('')
const logoutAction = useAction()
const sidebarOpen = ref(false), sidebarCollapsed = ref(false), modeDetailsOpen = ref(false)
const sectionNames: Record<string, string> = { papers: '文献工作台', research: '研究工作台', security: '查询与审计' }
const pageTitle = computed(() => sectionNames[String(route.path.split('/').pop())] || '项目总览')
const modeNote = computed(() => !health.value ? '暂未确认运行模式，请检查服务连接。' : persistentData.value ? '已注册数据适配器；持久化和重启恢复需由 A 实际验收。' : 'Mock 演示环境 · 合成数据，重启后丢失。')
function switchProject(value: string) {
  const section = String(route.path.split('/').pop())
  void router.push(`/projects/${value}/${sectionNames[section] ? section : 'papers'}`)
}
watch(() => route.path, () => { sidebarOpen.value = false })
const projectId = computed(() => String(route.params.projectId || ''))
const current = computed(() => projects.value.find(p => p.project_id === projectId.value))
const isResearch = computed(() => route.path.endsWith('/research'))
const modeLabel = computed(() => !health.value ? '运行模式未知' : Object.values(health.value.modes).every(mode => mode === 'mock') ? 'MOCK · 开发演示' : '适配器开发 · 待验收')
const unsubscribe = onUnauthorized(error => {
  if (user.value) { sessionNotice.value = `登录已失效，请重新登录；未自动重新提交任何操作。${error.requestId ? `请求编号：${error.requestId}` : ''}`; setIdentity(null) }
})
onUnmounted(unsubscribe)
watch([booting, user, projectId], () => {
  if (!booting.value && !user.value && projectId.value) { intendedRoute.value = route.fullPath; void router.replace('/') }
})
async function connect() {
  if (connecting.value) return
  connecting.value = true; startupError.value = ''
  try { health.value = await api.health() }
  catch (error) { health.value = null; startupError.value = formatError(error) }
  try {
    const identity = (await api.me()).user
    if (user.value?.user_id !== identity.user_id) setIdentity(identity)
    await refreshProjects()
  } catch (error) { if (!(error instanceof ApiError && error.status === 401)) startupError.value = formatError(error) }
  finally { connecting.value = false; booting.value = false }
}
onMounted(connect)
async function logout() {
  await logoutAction.run(async () => { sessionChanged(); await api.logout(); setIdentity(null); sidebarOpen.value = false; sessionNotice.value = ''; await router.push('/'); intendedRoute.value = '' })
}
</script>
<template>
  <div class="app-shell" :class="[user ? 'workspace-shell' : 'welcome-shell', { 'sidebar-collapsed': sidebarCollapsed }]">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <aside v-if="user" class="shell-sidebar" aria-label="侧边栏">
      <WorkspaceSidebar :logout-busy="logoutAction.busy.value" :collapsed="sidebarCollapsed" @toggle-collapse="sidebarCollapsed = !sidebarCollapsed" @logout="logout" />
    </aside>
    <el-drawer v-if="user" v-model="sidebarOpen" direction="ltr" :with-header="false" size="260px" class="navigation-drawer" aria-label="工作空间菜单">
      <WorkspaceSidebar :logout-busy="logoutAction.busy.value" @navigate="sidebarOpen = false" @logout="logout" />
    </el-drawer>
    <div class="shell-body">
      <header class="workspace-topbar">
        <div class="topbar-location">
          <button v-if="user" class="icon-button mobile-menu-button" aria-label="打开导航菜单" @click="sidebarOpen = true"><AppIcon name="menu" /></button>
          <RouterLink v-if="!user" to="/" class="welcome-brand"><span class="workspace-logo"><AppIcon name="book" :size="22" /></span><strong>PaperPilot <span>Secure</span></strong></RouterLink>
          <div v-else class="workspace-breadcrumb"><RouterLink v-if="projectId" to="/" aria-label="← 全部项目" title="全部项目"><AppIcon name="grid" :size="17" /></RouterLink><span v-else>工作空间</span><strong>{{ projectId ? current?.name || '项目工作台' : '项目总览' }}</strong><template v-if="projectId"><span class="breadcrumb-divider">/</span><h1 v-if="isResearch" class="workspace-page-title">{{ pageTitle }}</h1><span v-else class="breadcrumb-page">{{ pageTitle }}</span></template></div>
        </div>
        <div class="topbar-controls">
          <el-select v-if="projectId && projects.length" class="project-switcher mobile-project-switcher" :model-value="projectId" aria-label="切换项目" @change="switchProject"><el-option v-for="project in projects" :key="project.project_id" :value="project.project_id" :label="project.name" /></el-select>
          <button class="environment-chip" aria-label="查看运行说明" :title="modeLabel" @click="modeDetailsOpen = true"><span class="status-dot" /><span>{{ !health ? '模式未知' : Object.values(health.modes).every(value => value === 'mock') ? '演示数据' : '适配器数据' }}</span><AppIcon name="info" :size="14" /></button>
        </div>
      </header>
      <nav v-if="projectId && user" class="mobile-project-tabs" aria-label="项目快捷导航"><RouterLink :to="`/projects/${projectId}/papers`">文献</RouterLink><RouterLink :to="`/projects/${projectId}/research`">研究</RouterLink><RouterLink :to="`/projects/${projectId}/security`">查询与审计</RouterLink></nav>
      <main id="main-content" class="shell-main" :class="{ 'is-guest': !user, 'is-research': isResearch && user }" tabindex="-1">
        <div class="shell-feedback">
          <Feedback :busy="connecting && !booting" :error="startupError" retry-label="重新连接服务" @retry="connect" />
          <Feedback :error="logoutAction.error.value" />
          <el-alert v-if="sessionNotice && !user" :title="sessionNotice" type="warning" :closable="false" />
          <p v-if="booting" role="status">正在连接服务…</p>
        </div>
        <RouterView v-if="!booting && (!projectId || user)" :key="`${user?.user_id || 'guest'}:${route.path}`" />
      </main>
      <footer v-if="user && !isResearch" class="workspace-footer"><span>PaperPilot</span></footer>
    </div>
    <el-dialog v-model="modeDetailsOpen" title="当前运行说明" width="480px">
      <div class="mode-details"><span class="mode-details-icon"><AppIcon name="shield" :size="28" /></span><h3>{{ modeLabel }}</h3><p>{{ modeNote }}</p><p>{{ health?.limitations.includes('development_identity_only') ? '开发身份不验证真实密码。' : '身份通过服务端会话验证。' }}当前注册模式不代表真实 AI 或 SQL 安全能力已通过验收。</p><p class="muted">具体结果仍需核对 Mock 与合成来源标记。正式数据持久化、AI 质量及安全能力由对应模块实际验收。</p></div>
    </el-dialog>
  </div>
</template>
