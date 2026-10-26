<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from './api/client'
import { booting, persistentData, projects, refreshProjects, user } from './state'
const route = useRoute(), router = useRouter(), startupError = ref('')
const projectId = computed(() => String(route.params.projectId || ''))
const current = computed(() => projects.value.find(p => p.project_id === projectId.value))
onMounted(async () => {
  try { const health = await api.health(); persistentData.value = health.modes.a !== 'mock'; user.value = (await api.me()).user; await refreshProjects() }
  catch (e) { if (!(e instanceof ApiError && e.code === 'UNAUTHENTICATED')) startupError.value = '后端暂不可用，请检查启动状态后刷新页面' }
  finally { booting.value = false }
})
async function logout() { await api.logout(); user.value = null; projects.value = []; await router.push('/') }
</script>
<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink to="/" class="brand"><span class="brand-icon">P</span><span>PaperPilot <b>Secure</b></span></RouterLink>
      <div class="header-right"><el-tag type="warning" effect="plain">MOCK · 开发演示</el-tag><span v-if="user">{{ user.display_name }}</span><el-button v-if="user" text @click="logout">退出</el-button></div>
    </header>
    <div class="demo-notice">{{ persistentData ? '当前数据由已注册的持久化适配器保存；登录会话在重启后需重新建立。' : '当前使用合成数据与内存适配器；重启后数据丢失。' }} AI 回答与统计结果仅演示契约，不具备真实模型或 SQL 安全防护能力。</div>
    <main>
      <el-alert v-if="startupError" :title="startupError" type="error" :closable="false" />
      <p v-if="booting" role="status">正在连接服务…</p>
      <template v-else>
        <div v-if="projectId" class="project-nav">
          <RouterLink to="/">← 全部项目</RouterLink><strong>{{ current?.name || '项目工作台' }}</strong>
          <nav><RouterLink :to="`/projects/${projectId}/papers`">文献</RouterLink><RouterLink :to="`/projects/${projectId}/research`">研究</RouterLink><RouterLink :to="`/projects/${projectId}/security`">查询与审计</RouterLink></nav>
        </div>
        <RouterView :key="route.path" />
      </template>
    </main>
    <footer>PaperPilot Secure · Phase 1 / 工程骨架与契约演示</footer>
  </div>
</template>
