import { computed, ref } from 'vue'
import { api, sessionChanged, type Models } from './api/client'
import { formatError } from './api/errors'

export const user = ref<Models['User'] | null>(null)
export const projects = ref<Models['Project'][]>([])
export const booting = ref(true)
export const intendedRoute = ref('')
export const health = ref<Models['Health'] | null>(null)
export const passwordAuth = computed(() => health.value?.limitations.includes('authentication_credentials') ?? false)
export const persistentData = computed(() => !!health.value && health.value.modes.a !== 'mock')
export const aiMock = computed(() => health.value?.modes.c === 'mock')
export const gatewayMock = computed(() => health.value?.modes.d === 'mock')
export const projectsLoading = ref(false), projectsLoaded = ref(false), projectsError = ref('')
let projectSequence = 0
export function setIdentity(identity: Models['User'] | null) {
  sessionChanged(); projectSequence++
  user.value = identity; projects.value = []; projectsLoading.value = false; projectsLoaded.value = false; projectsError.value = ''
}
export async function refreshProjects(): Promise<boolean> {
  if (!user.value) return false
  const current = ++projectSequence, owner = user.value.user_id
  projectsLoading.value = true; projectsLoaded.value = false; projectsError.value = ''; projects.value = []
  try {
    const result = await api.projects()
    if (current !== projectSequence || user.value?.user_id !== owner) return false
    projects.value = result.items; projectsLoaded.value = true
    return true
  } catch (error) {
    if (current === projectSequence && user.value?.user_id === owner) projectsError.value = formatError(error)
    return false
  } finally { if (current === projectSequence) projectsLoading.value = false }
}
