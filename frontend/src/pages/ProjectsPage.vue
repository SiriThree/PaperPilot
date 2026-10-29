<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Models } from '../api/client'
import { health, intendedRoute, projects, projectsError, projectsLoaded, projectsLoading, refreshProjects, setIdentity, user } from '../state'
import { useAction } from '../composables/useAction'
import { useResource } from '../composables/useResource'
import Feedback from '../components/Feedback.vue'
import AppIcon from '../components/AppIcon.vue'
const router = useRouter(), { busy, error, run } = useAction()
const username = ref('alice'), password = ref(''), loginTouched = ref(false), name = ref(''), selected = ref<Models['Project'] | null>(null), newMember = ref('bob')
const credentialMode = computed(() => health.value?.limitations.includes('authentication_credentials') === true)
const demoIdentity = computed(() => health.value?.limitations.includes('development_identity_only') === true)
watch(credentialMode, value => { username.value = value ? '' : 'alice'; password.value = ''; loginTouched.value = false }, { immediate: true })
const memberResource = useResource<Models['Member'][]>([], async () => selected.value ? (await api.members(selected.value.project_id)).items : [])
const members = memberResource.data
const nameTouched = ref(false)
const ownedProjects = computed(() => projects.value.filter(project => project.role === 'owner').length)
const countsReady = computed(() => projectsLoaded.value && !projectsError.value && !projectsLoading.value)
const identities: Record<string, { name: string; role: string; description: string }> = {
  alice: { name: 'Alice', role: '项目负责人', description: '管理项目与成员，体验完整研究流程。' },
  bob: { name: 'Bob', role: '项目成员', description: '参与文献整理、研究与报告编辑。' },
  eve: { name: 'Eve', role: '独立项目成员', description: '体验不同项目之间的访问边界。' },
}
const identity = computed(() => identities[username.value]!)
const welcomeModeHint = computed(() => !health.value ? '运行模式尚未确认，请先检查服务连接。'
  : Object.values(health.value.modes).every(mode => mode === 'mock') ? 'Mock 数据与合成结果用于体验，重启后丢失。'
  : '已注册适配器；数据、AI 与安全能力仍需实际验收。')
const nameError = computed(() => nameTouched.value && !name.value.trim() ? '请输入项目名称。' : '')
async function login() {
  loginTouched.value = true
  if (!username.value.trim() || (credentialMode.value && !password.value)) return
  loginTouched.value = false
  await run(async () => {
    try { await api.login(username.value.trim(), credentialMode.value ? password.value : undefined) }
    finally { password.value = '' }
    setIdentity((await api.me()).user)
    await refreshProjects()
    const target = intendedRoute.value
    intendedRoute.value = ''
    const projectId = /^\/projects\/([^/]+)\/(?:papers|research|security)(?:[?#]|$)/.exec(target)?.[1]
    if (projectId && projects.value.some(project => project.project_id === projectId)) await router.push(target)
  })
}
async function create() { nameTouched.value = true; if (!name.value.trim()) return; await run(async () => { const p = await api.createProject(name.value); name.value = ''; nameTouched.value = false; await refreshProjects(); await router.push(`/projects/${p.project_id}/papers`) }) }
async function showMembers(p: Models['Project']) { selected.value = p; await memberResource.load() }
function closeMembers() { selected.value = null; memberResource.reset() }
async function add() { const project = selected.value; if (project) await run(async () => { await api.addMember(project.project_id, newMember.value); if (selected.value?.project_id === project.project_id) await memberResource.load() }) }
async function remove(id: string) { const project = selected.value; if (project) await run(async () => { await api.removeMember(project.project_id, id); if (selected.value?.project_id === project.project_id) await memberResource.load() }) }
</script>
<template>
  <div v-if="!user" class="welcome-layout">
    <section class="welcome-story" aria-label="PaperPilot 介绍">
      <div class="story-content">
        <span class="story-eyebrow"><span /> YOUR RESEARCH, CONNECTED</span>
        <h1>让每一个结论，<br>都有据可循<span class="hero-period">。</span></h1>
        <p class="story-description">把分散的文献、来源与想法，<br class="desktop-break">汇集到一个清晰的研究工作空间。</p>
        <div class="research-illustration" aria-label="文献、来源片段与研究报告的流程示意">
          <svg viewBox="0 0 520 264" fill="none" aria-hidden="true">
            <path d="M66 226H468M66 42H468" stroke="#334459" stroke-dasharray="3 6" />
            <rect x="71" y="60" width="155" height="160" rx="12" fill="#20354B" stroke="#486177" transform="rotate(-7 71 60)" />
            <rect x="74" y="37" width="164" height="180" rx="12" fill="#F5F8FA" />
            <rect x="94" y="58" width="30" height="7" rx="3.5" fill="#16887D" />
            <rect x="94" y="83" width="120" height="9" rx="3" fill="#243F53" /><rect x="94" y="99" width="84" height="9" rx="3" fill="#243F53" />
            <path d="M94 131H214M94 143H203M94 155H214M94 181H184" stroke="#C5D0D6" stroke-width="5" stroke-linecap="round" />
            <rect x="90" y="151" width="128" height="10" rx="3" fill="#B3E2D9" fill-opacity=".85" />
            <path d="M218 156H270Q286 156 286 140V126H315" stroke="#61C9B6" stroke-width="2" stroke-dasharray="4 5" />
            <rect x="278" y="65" width="176" height="78" rx="10" fill="#243D50" stroke="#426175" />
            <circle cx="299" cy="86" r="4" fill="#64CDB6" /><path d="M312 85H388M297 104H431M297 118H409" stroke="#7291A2" stroke-width="5" stroke-linecap="round" />
            <rect x="307" y="158" width="163" height="66" rx="10" fill="#EFF6F3" /><rect x="323" y="175" width="23" height="22" rx="5" fill="#D1E9E0" />
            <path d="m329 186 4 4 7-8" stroke="#24836D" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /><path d="M358 179H450M358 192H424M323 209H433" stroke="#AABEB5" stroke-width="5" stroke-linecap="round" />
            <circle cx="247" cy="59" r="13" fill="#2B645E" /><path d="m242 59 3 3 6-7" stroke="#ABEDDB" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div class="illustration-labels"><span>整理文献</span><span>关联来源</span><span>沉淀报告</span></div>
        </div>
        <div class="story-benefits"><span><AppIcon name="book" :size="16" />文献集中管理</span><span><AppIcon name="quote" :size="16" />引用关联来源</span><span><AppIcon name="shield" :size="16" />项目访问边界</span></div>
      </div>
      <p class="story-caption">PAPERPILOT SECURE <span>为严谨的研究过程而设计</span></p>
    </section>
    <section class="welcome-entry">
      <div class="entry-content">
        <span class="entry-kicker">开始你的研究工作</span>
        <h2>{{ credentialMode ? '登录' : demoIdentity ? '开发演示登录' : '连接服务' }}</h2>
        <p class="entry-description">{{ credentialMode ? '使用预置账号和密码登录研究工作空间。' : demoIdentity ? '选择一个身份，探索文献到研究报告的完整流程。' : '请先连接服务以确认身份认证方式。' }}</p>
        <Feedback :busy="busy" :error="error" />
        <el-form v-if="demoIdentity" label-position="top" class="identity-form" @submit.prevent="login">
          <el-form-item label="演示身份"><el-select v-model="username" aria-label="演示身份" size="large"><el-option v-for="u in ['alice','bob','eve']" :key="u" :label="u" :value="u" /></el-select></el-form-item>
          <div class="identity-preview"><span class="identity-avatar">{{ identity.name.slice(0, 1) }}</span><div><strong>{{ identity.name }}<span>{{ identity.role }}</span></strong><p>{{ identity.description }}</p></div></div>
          <el-button type="primary" native-type="submit" size="large" class="enter-workspace" :loading="busy">进入演示<AppIcon name="arrow" :size="18" /></el-button>
        </el-form>
        <el-form v-else-if="credentialMode" label-position="top" class="identity-form" @submit.prevent="login">
          <el-form-item label="账号" :error="loginTouched && !username.trim() ? '请输入账号。' : ''"><el-input v-model="username" aria-label="账号" autocomplete="username" size="large" /></el-form-item>
          <el-form-item label="密码" :error="loginTouched && !password && !busy ? '请输入密码。' : ''"><el-input v-model="password" aria-label="密码" type="password" show-password autocomplete="current-password" size="large" /></el-form-item>
          <el-button type="primary" native-type="submit" size="large" class="enter-workspace" :loading="busy" :disabled="busy">登录<AppIcon name="arrow" :size="18" /></el-button>
        </el-form>
        <div v-if="demoIdentity" class="login-disclosure"><AppIcon name="info" :size="17" /><p>仅切换演示身份，不验证真实密码。<br>{{ welcomeModeHint }}</p></div>
      </div>
      <div class="entry-footer"><span class="entry-footer-line" />文献有序 · 引用可溯 · 研究留痕</div>
    </section>
  </div>
  <div v-else class="projects-home">
    <div class="home-page-heading"><div><span class="eyebrow">YOUR WORKSPACE</span><h1>你的研究项目</h1><p class="muted">从一个问题出发，让文献、证据与研究成果保持关联。</p></div><el-button class="refresh-projects" :disabled="projectsLoading" @click="refreshProjects"><AppIcon name="refresh" :size="16" />刷新项目</el-button></div>
    <div class="project-metrics" aria-label="项目概览">
      <div class="project-metric"><span class="metric-icon"><AppIcon name="folder" /></span><div><span>已加入项目</span><strong data-testid="project-count">{{ countsReady ? projects.length : '—' }}<small>个项目</small></strong></div></div>
      <div class="project-metric"><span class="metric-icon"><AppIcon name="shield" /></span><div><span>我负责的项目</span><strong>{{ countsReady ? ownedProjects : '—' }}<small>负责人</small></strong></div></div>
      <div class="project-metric"><span class="metric-icon"><AppIcon name="people" /></span><div><span>参与协作</span><strong>{{ countsReady ? projects.length - ownedProjects : '—' }}<small>成员身份</small></strong></div></div>
    </div>
    <Feedback :busy="projectsLoading" :error="projectsError" retry-label="重新加载项目" @retry="refreshProjects" />
    <Feedback :busy="busy" :error="error" />
    <div class="projects-home-layout">
      <div class="projects-primary">
        <section class="create-project-panel"><div class="create-project-title"><span class="create-icon"><AppIcon name="plus" :size="18" /></span><div><h2>创建新项目</h2><p>为一个研究问题建立专属空间。</p></div></div><form @submit.prevent="create"><label class="field-label" for="project-name">项目名称</label><div class="inline-form"><el-input id="project-name" v-model="name" placeholder="新项目名称" aria-label="新项目名称" maxlength="100" :aria-invalid="!!nameError" aria-describedby="project-name-help" @blur="nameTouched = true" /><el-button type="primary" native-type="submit" :disabled="busy" :loading="busy"><AppIcon name="plus" :size="16" />创建项目</el-button></div><p v-if="nameError" class="field-error" role="alert">{{ nameError }}</p><p id="project-name-help" class="muted">最多 100 个字符。创建者为负责人，可管理成员。</p></form></section>
        <div class="project-list-heading"><h2>全部项目<span v-if="countsReady">{{ projects.length }}</span></h2><span>按项目组织研究</span></div>
        <el-empty v-if="projectsLoaded && !projectsLoading && !projectsError && !projects.length" description="尚无项目，可以创建一个项目开始" />
        <div class="project-grid"><article v-for="p in projects" :key="p.project_id" class="panel project-card"><div class="card-top"><span class="project-folder"><AppIcon name="folder" :size="23" /></span><span class="project-role" :class="{ 'is-owner': p.role === 'owner' }">{{ p.role === 'owner' ? '负责人' : '成员' }}</span></div><h2>{{ p.name }}</h2><p class="muted">{{ p.description || '在这里整理文献、关联证据，并记录研究成果。' }}</p><div class="card-actions"><el-button class="project-open" text @click="router.push(`/projects/${p.project_id}/papers`)">进入工作台<AppIcon name="arrow" :size="17" /></el-button><el-button v-if="p.role === 'owner'" text class="project-members" @click="showMembers(p)"><AppIcon name="people" :size="15" />管理成员</el-button></div></article></div>
      </div>
      <aside class="research-guide" aria-label="研究流程指引"><div class="guide-heading"><span class="guide-icon"><AppIcon name="book" :size="22" /></span><span>RESEARCH GUIDE</span></div><h2>从文献，到你的见解。</h2><p class="muted">把每一步研究，留在同一个项目中。</p><ol><li><span>01</span><div><h3>建立文献库</h3><p>导入论文，整理项目内的研究材料。</p></div></li><li><span>02</span><div><h3>追溯每条来源</h3><p>检索相关片段，对照页码核验引用。</p></div></li><li><span>03</span><div><h3>记录研究成果</h3><p>编辑报告，保存正文与引用快照。</p></div></li></ol><div class="guide-reminder"><AppIcon name="shield" :size="18" /><p>生成内容仍需人工核验。<br>让结论与来源保持关联。</p></div></aside>
    </div>
    <el-dialog :model-value="!!selected" title="项目成员" width="560px" @close="closeMembers"><template v-if="selected"><p class="muted">{{ selected.name }}</p><Feedback :busy="memberResource.loading.value" :error="memberResource.error.value" retry-label="重新加载成员" @retry="memberResource.load" /><div class="inline-form"><el-input v-model="newMember" placeholder="已有演示账户名" aria-label="添加成员账户" /><el-button :disabled="busy || !newMember.trim()" @click="add">添加成员</el-button></div><el-table v-if="memberResource.loaded.value" :data="members"><el-table-column prop="user.username" label="账户" /><el-table-column label="角色"><template #default="scope">{{ scope.row.role === 'owner' ? '负责人' : '成员' }}</template></el-table-column><el-table-column label="操作"><template #default="scope"><el-button v-if="scope.row.role !== 'owner'" :disabled="busy" type="danger" text @click="remove(scope.row.user.user_id)">移除</el-button></template></el-table-column></el-table><el-empty v-if="memberResource.loaded.value && !members.length" description="当前没有可展示成员" /></template></el-dialog>
  </div>
</template>
