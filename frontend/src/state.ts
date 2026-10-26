import { ref } from 'vue'
import { api, type Models } from './api/client'

export const user = ref<Models['User'] | null>(null)
export const projects = ref<Models['Project'][]>([])
export const booting = ref(true)
export const persistentData = ref(false)
export async function refreshProjects() { projects.value = (await api.projects()).items }
