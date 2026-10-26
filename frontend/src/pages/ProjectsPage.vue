<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Models } from '../api/client'
import { projects, refreshProjects, user } from '../state'
import { useAction } from '../composables/useAction'
import Feedback from '../components/Feedback.vue'
const router = useRouter(), { busy, error, run } = useAction()
const username = ref('alice'), name = ref(''), selected = ref<Models['Project'] | null>(null), members = ref<Models['Member'][]>([]), newMember = ref('bob')
async function login() { await run(async () => { user.value = (await api.login(username.value)).user; await refreshProjects() }) }
async function create() { await run(async () => { const p = await api.createProject(name.value); name.value = ''; await refreshProjects(); await router.push(`/projects/${p.project_id}/papers`) }) }
async function showMembers(p: Models['Project']) { await run(async () => { selected.value = p; members.value = (await api.members(p.project_id)).items }) }
async function add() { if (selected.value) await run(async () => { await api.addMember(selected.value!.project_id, newMember.value); members.value = (await api.members(selected.value!.project_id)).items }) }
async function remove(id: string) { if (selected.value) await run(async () => { await api.removeMember(selected.value!.project_id, id); members.value = (await api.members(selected.value!.project_id)).items }) }
</script>
<template>
  <div class="page-heading"><span class="eyebrow">RESEARCH WORKSPACE</span><h1>{{ user ? '你的研究项目' : '让文献与证据保持关联' }}</h1><p class="muted">从项目出发，整理文献、核验来源，并保存研究过程。</p></div>
  <Feedback :busy="busy" :error="error" />
  <section v-if="!user" class="panel login-panel">
    <h2>开发演示登录</h2><p class="muted">仅切换演示身份，不验证真实密码。Alice 为负责人，Bob 为成员，Eve 属于另一项目。</p>
    <el-form @submit.prevent="login"><el-form-item label="演示身份"><el-select v-model="username" aria-label="演示身份"><el-option v-for="u in ['alice','bob','eve']" :key="u" :label="u" :value="u" /></el-select></el-form-item><el-button type="primary" native-type="submit" :loading="busy">进入演示</el-button></el-form>
  </section>
  <template v-else>
    <section class="panel inline-form"><el-input v-model="name" placeholder="新项目名称" aria-label="新项目名称" maxlength="100" /><el-button type="primary" :disabled="!name.trim()" :loading="busy" @click="create">创建项目</el-button></section>
    <el-empty v-if="!projects.length" description="尚无项目，可以创建一个项目开始" />
    <div class="project-grid"><article v-for="p in projects" :key="p.project_id" class="panel project-card"><div class="card-top"><span class="folder-icon">▤</span><el-tag :type="p.role === 'owner' ? 'success' : 'info'">{{ p.role === 'owner' ? '负责人' : '成员' }}</el-tag></div><h2>{{ p.name }}</h2><p class="muted">{{ p.description || '开始整理这个项目的文献与证据' }}</p><div class="card-actions"><el-button type="primary" @click="router.push(`/projects/${p.project_id}/papers`)">进入工作台</el-button><el-button v-if="p.role === 'owner'" @click="showMembers(p)">管理成员</el-button></div></article></div>
    <el-dialog :model-value="!!selected" title="项目成员" @close="selected = null"><template v-if="selected"><div class="inline-form"><el-input v-model="newMember" placeholder="已有演示账户名" aria-label="添加成员账户" /><el-button @click="add">添加成员</el-button></div><el-table :data="members"><el-table-column prop="user.username" label="账户" /><el-table-column prop="role" label="角色" /><el-table-column label="操作"><template #default="scope"><el-button v-if="scope.row.role !== 'owner'" type="danger" text @click="remove(scope.row.user.user_id)">移除</el-button></template></el-table-column></el-table></template></el-dialog>
  </template>
</template>
