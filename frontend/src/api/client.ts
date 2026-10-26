import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

export type Models = components['schemas']
export class ApiError extends Error {
  constructor(public code: string, message: string, public requestId?: string, public status?: number) { super(message) }
}
const client = createClient<paths>({ baseUrl: '', credentials: 'same-origin', headers: { 'X-PaperPilot-Client': 'web' } })
async function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): Promise<T> {
  if (!result.response.ok || result.error) {
    const e = result.error as Partial<Models['ErrorResponse']> | undefined
    throw new ApiError(e?.code || 'REQUEST_FAILED', e?.message || '请求失败，请稍后重试', e?.request_id, result.response.status)
  }
  return result.data as T
}
const clientHeader = { 'X-PaperPilot-Client': 'web' as const }
function params(projectId: string) { return { path: { project_id: projectId }, header: clientHeader } }
function readParams(projectId: string) { return { path: { project_id: projectId } } }
const writeHeaders = (key: string = crypto.randomUUID()) => ({ 'Idempotency-Key': key })
export const api = {
  health: () => client.GET('/api/v1/health').then(unwrap),
  me: () => client.GET('/api/v1/auth/me').then(unwrap),
  login: (username: string) => client.POST('/api/v1/auth/login', { params: { header: clientHeader }, body: { username } }).then(unwrap),
  logout: () => client.POST('/api/v1/auth/logout', { params: { header: clientHeader } }).then(unwrap),
  projects: () => client.GET('/api/v1/projects').then(unwrap),
  createProject: (name: string) => client.POST('/api/v1/projects', { params: { header: clientHeader }, body: { name, description: '' } }).then(unwrap),
  members: (projectId: string) => client.GET('/api/v1/projects/{project_id}/members', { params: readParams(projectId) }).then(unwrap),
  addMember: (projectId: string, username: string) => client.POST('/api/v1/projects/{project_id}/members', { params: params(projectId), body: { username } }).then(unwrap),
  removeMember: (projectId: string, userId: string) => client.DELETE('/api/v1/projects/{project_id}/members/{user_id}', { params: { path: { project_id: projectId, user_id: userId }, header: clientHeader } }).then(unwrap),
  papers: (projectId: string, yearFrom?: number, yearTo?: number) => client.GET('/api/v1/projects/{project_id}/papers', { params: { ...readParams(projectId), query: { year_from: yearFrom, year_to: yearTo } } }).then(unwrap),
  paper: (projectId: string, paperId: string) => client.GET('/api/v1/projects/{project_id}/papers/{paper_id}', { params: { path: { project_id: projectId, paper_id: paperId } } }).then(unwrap),
  upload: async (projectId: string, file: File, title: string, key?: string) => {
    const form = new FormData(); form.append('file', file); form.append('title', title)
    return client.POST('/api/v1/projects/{project_id}/papers', {
      params: params(projectId), body: { file: '', title },
      bodySerializer: () => form, headers: writeHeaders(key), signal: AbortSignal.timeout(15000),
    }).then(unwrap)
  },
  task: (projectId: string, taskId: string, signal?: AbortSignal) => client.GET('/api/v1/projects/{project_id}/tasks/{task_id}', { params: { path: { project_id: projectId, task_id: taskId } }, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000) }).then(unwrap),
  retry: (projectId: string, taskId: string) => client.POST('/api/v1/projects/{project_id}/tasks/{task_id}/retry', { params: { path: { project_id: projectId, task_id: taskId }, header: clientHeader }, signal: AbortSignal.timeout(15000) }).then(unwrap),
  search: (projectId: string, body: Models['SearchRequest']) => client.POST('/api/v1/projects/{project_id}/search', { params: params(projectId), body }).then(unwrap),
  answer: (projectId: string, question: string, key?: string, paperIds: string[] = []) => client.POST('/api/v1/projects/{project_id}/answers', { params: params(projectId), body: { question, paper_ids: paperIds }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap),
  evidence: (projectId: string, paperId: string, key?: string) => client.POST('/api/v1/projects/{project_id}/papers/{paper_id}/evidence', { params: { path: { project_id: projectId, paper_id: paperId }, header: clientHeader }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap),
  draft: (projectId: string, question: string, key?: string, paperIds: string[] = []) => client.POST('/api/v1/projects/{project_id}/report-drafts', { params: params(projectId), body: { question, paper_ids: paperIds }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap),
  save: (projectId: string, body: Models['ReportSave'], key?: string) => client.POST('/api/v1/projects/{project_id}/reports', { params: params(projectId), body, headers: writeHeaders(key) }).then(unwrap),
  reports: (projectId: string) => client.GET('/api/v1/projects/{project_id}/reports', { params: readParams(projectId) }).then(unwrap),
  versions: (projectId: string, reportId: string) => client.GET('/api/v1/projects/{project_id}/reports/{report_id}/versions', { params: { path: { project_id: projectId, report_id: reportId } } }).then(unwrap),
  secureQuery: (projectId: string, question: string) => client.POST('/api/v1/projects/{project_id}/secure-queries', { params: params(projectId), body: { question } }).then(unwrap),
  audit: (projectId: string, query: { action?: string; decision?: string; actor_id?: string; since?: string; until?: string; offset: number; limit: number }) => client.GET('/api/v1/projects/{project_id}/audit-events', { params: { ...readParams(projectId), query } }).then(unwrap),
}

// Compatibility helper only; the task pages use useTaskRecovery for retained IDs and unknown states.
export async function waitTask(projectId: string, taskId: string, signal?: AbortSignal): Promise<Models['Task']> {
  for (let i = 0; i < 120; i++) {
    if (signal?.aborted) throw new Error('页面已切换，停止等待')
    const task = await api.task(projectId, taskId)
    if (task.status === 'failed') throw new ApiError(task.error?.code || 'TASK_FAILED', task.error?.message || '任务处理失败', task.request_id)
    if (task.status === 'succeeded') return task
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('等待超过 60 秒，请稍后查询任务状态')
}
