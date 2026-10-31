import { createRouter, createWebHistory } from 'vue-router'
import ProjectsPage from './pages/ProjectsPage.vue'
import PapersPage from './pages/PapersPage.vue'
import ResearchPage from './pages/ResearchPage.vue'
import SecurityPage from './pages/SecurityPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: ProjectsPage },
    { path: '/projects/:projectId/papers', component: PapersPage },
    { path: '/projects/:projectId/research', component: ResearchPage },
    { path: '/projects/:projectId/security', component: SecurityPage },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
