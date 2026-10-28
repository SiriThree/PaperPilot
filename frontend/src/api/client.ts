import createClient from 'openapi-fetch'
import type { components, paths } from './schema'
import { ApiError, httpError, normalizeError, responseRequestId, taskError } from './errors'
export { ApiError } from './errors'

export type Models = components['schemas']
let sessionEpoch = 0
const unauthorized = new Set<(error: ApiError) => void>()
export function sessionChanged() { sessionEpoch++ }
export function onUnauthorized(handler: (error: ApiError) => void) { unauthorized.add(handler); return () => { unauthorized.delete(handler) } }
async function transport(request: Request): Promise<Response> {
  const epoch = sessionEpoch
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15000)])
  const readOnly = request.method === 'GET' || new URL(request.url).pathname.endsWith('/search')
  let response: Response | undefined
  let body: string
  // These typed API endpoints return JSON. Include reading its body in the
  // transport deadline, so a stalled body is not misreported as invalid JSON.
  try { response = await fetch(new Request(request, { signal })); body = await response.text() }
  catch {
    // A received 401 is a definite authorization refusal even if its body stalls.
    if (response?.status === 401) {
      const error = httpError(response, undefined)
      if (epoch === sessionEpoch) for (const handler of unauthorized) handler(error)
      throw error
    }
    const requestId = response ? responseRequestId(response) : undefined
    if (signal.aborted && signal.reason?.name === 'TimeoutError') throw new ApiError('REQUEST_TIMEOUT', readOnly ? '查询超时，请重新查询原资源' : '请求超时，提交结果尚未确认；请先核对原请求', requestId)
    if (signal.aborted) throw new ApiError('REQUEST_CANCELLED', '请求已取消，未自动重新提交', requestId)
    throw new ApiError('NETWORK_ERROR', readOnly ? '网络暂时不可用，请稍后重新查询' : '连接异常，提交结果尚未确认；请先核对原请求', requestId)
  }
  let payload: unknown
  try { payload = JSON.parse(body) } catch { payload = undefined }
  if (!response.ok) {
    const error = httpError(response, payload)
    if (response.status === 401 && epoch === sessionEpoch) for (const handler of unauthorized) handler(error)
    throw error
  }
  if (payload === undefined) throw new ApiError('INVALID_RESPONSE', '服务响应格式不正确，请保留当前输入后重新查询', responseRequestId(response), response.status)
  return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
}
const client = createClient<paths>({ baseUrl: '', credentials: 'same-origin', headers: { 'X-PaperPilot-Client': 'web' }, fetch: transport })
async function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): Promise<T> {
  if (!result.response.ok || result.error) {
    throw httpError(result.response, result.error)
  }
  if (result.data === undefined || result.data === null) throw new ApiError('INVALID_RESPONSE', '服务未返回有效结果，请重新查询', responseRequestId(result.response), result.response.status)
  if (typeof result.data === 'object' && 'code' in result.data && 'message' in result.data) throw new ApiError('INVALID_RESPONSE', '服务返回了异常结果，请保留当前输入后重新查询', responseRequestId(result.response, result.data), result.response.status)
  return result.data as T
}
async function healthResult(result: { data?: Models['Health']; error?: unknown; response: Response }) {
  const data = await unwrap(result)
  if (data.status !== 'ok' || typeof data.mock !== 'boolean' || !data.modes || typeof data.modes !== 'object'
    || !['a', 'c', 'd'].every(key => typeof data.modes[key] === 'string')
    || !Array.isArray(data.limitations) || data.limitations.some(value => typeof value !== 'string')) {
    throw new ApiError('INVALID_RESPONSE', '运行状态响应不完整，请重新连接服务', responseRequestId(result.response), result.response.status)
  }
  return data
}
const clientHeader = { 'X-PaperPilot-Client': 'web' as const }
function params(projectId: string) { return { path: { project_id: projectId }, header: clientHeader } }
function readParams(projectId: string) { return { path: { project_id: projectId } } }
const writeHeaders = (key: string = crypto.randomUUID()) => ({ 'Idempotency-Key': key })
export const api = {
  health: () => client.GET('/api/v1/health').then(healthResult).catch(normalizeError),
  me: () => client.GET('/api/v1/auth/me').then(unwrap).catch(normalizeError),
  login: (username: string) => client.POST('/api/v1/auth/login', { params: { header: clientHeader }, body: { username } }).then(unwrap).catch(normalizeError),
  logout: () => client.POST('/api/v1/auth/logout', { params: { header: clientHeader } }).then(unwrap).catch(normalizeError),
  projects: () => client.GET('/api/v1/projects').then(unwrap).catch(normalizeError),
  createProject: (name: string) => client.POST('/api/v1/projects', { params: { header: clientHeader }, body: { name, description: '' } }).then(unwrap).catch(normalizeError),
  members: (projectId: string) => client.GET('/api/v1/projects/{project_id}/members', { params: readParams(projectId) }).then(unwrap).catch(normalizeError),
  addMember: (projectId: string, username: string) => client.POST('/api/v1/projects/{project_id}/members', { params: params(projectId), body: { username } }).then(unwrap).catch(normalizeError),
  removeMember: (projectId: string, userId: string) => client.DELETE('/api/v1/projects/{project_id}/members/{user_id}', { params: { path: { project_id: projectId, user_id: userId }, header: clientHeader } }).then(unwrap).catch(normalizeError),
  papers: (projectId: string, yearFrom?: number, yearTo?: number) => client.GET('/api/v1/projects/{project_id}/papers', { params: { ...readParams(projectId), query: { year_from: yearFrom, year_to: yearTo } } }).then(unwrap).catch(normalizeError),
  paper: (projectId: string, paperId: string) => client.GET('/api/v1/projects/{project_id}/papers/{paper_id}', { params: { path: { project_id: projectId, paper_id: paperId } } }).then(unwrap).catch(normalizeError),
  removePaper: (projectId: string, paperId: string) => client.DELETE('/api/v1/projects/{project_id}/papers/{paper_id}', { params: { path: { project_id: projectId, paper_id: paperId }, query: { confirm_source_impact: true }, header: clientHeader } }).then(unwrap).catch(normalizeError),
  upload: async (projectId: string, file: File, title: string, key?: string) => {
    const form = new FormData(); form.append('file', file); form.append('title', title)
    return client.POST('/api/v1/projects/{project_id}/papers', {
      params: params(projectId), body: { file: '', title },
      bodySerializer: () => form, headers: writeHeaders(key), signal: AbortSignal.timeout(15000),
    }).then(unwrap).catch(normalizeError)
  },
  task: (projectId: string, taskId: string, signal?: AbortSignal) => client.GET('/api/v1/projects/{project_id}/tasks/{task_id}', { params: { path: { project_id: projectId, task_id: taskId } }, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000) }).then(unwrap).catch(normalizeError),
  retry: (projectId: string, taskId: string) => client.POST('/api/v1/projects/{project_id}/tasks/{task_id}/retry', { params: { path: { project_id: projectId, task_id: taskId }, header: clientHeader }, signal: AbortSignal.timeout(15000) }).then(unwrap).catch(normalizeError),
  search: (projectId: string, body: Models['SearchRequest']) => client.POST('/api/v1/projects/{project_id}/search', { params: params(projectId), body }).then(unwrap).catch(normalizeError),
  answer: (projectId: string, question: string, key?: string, paperIds: string[] = []) => client.POST('/api/v1/projects/{project_id}/answers', { params: params(projectId), body: { question, paper_ids: paperIds }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap).catch(normalizeError),
  evidence: (projectId: string, paperId: string, key?: string) => client.POST('/api/v1/projects/{project_id}/papers/{paper_id}/evidence', { params: { path: { project_id: projectId, paper_id: paperId }, header: clientHeader }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap).catch(normalizeError),
  evidenceHistory: (projectId: string, paperId: string) => client.GET('/api/v1/projects/{project_id}/papers/{paper_id}/evidence', { params: { path: { project_id: projectId, paper_id: paperId } } }).then(unwrap).catch(normalizeError),
  draft: (projectId: string, question: string, key?: string, paperIds: string[] = []) => client.POST('/api/v1/projects/{project_id}/report-drafts', { params: params(projectId), body: { question, paper_ids: paperIds }, headers: writeHeaders(key), signal: AbortSignal.timeout(15000) }).then(unwrap).catch(normalizeError),
  save: (projectId: string, body: Models['ReportSave'], key?: string) => client.POST('/api/v1/projects/{project_id}/reports', { params: params(projectId), body, headers: writeHeaders(key) }).then(unwrap).catch(normalizeError),
  reports: (projectId: string) => client.GET('/api/v1/projects/{project_id}/reports', { params: readParams(projectId) }).then(unwrap).catch(normalizeError),
  versions: (projectId: string, reportId: string) => client.GET('/api/v1/projects/{project_id}/reports/{report_id}/versions', { params: { path: { project_id: projectId, report_id: reportId } } }).then(unwrap).catch(normalizeError),
  secureQuery: (projectId: string, question: string) => client.POST('/api/v1/projects/{project_id}/secure-queries', { params: params(projectId), body: { question } }).then(unwrap).catch(normalizeError),
  audit: (projectId: string, query: { action?: string; decision?: string; actor_id?: string; since?: string; until?: string; offset: number; limit: number }) => client.GET('/api/v1/projects/{project_id}/audit-events', { params: { ...readParams(projectId), query } }).then(unwrap).catch(normalizeError),
}

// Compatibility helper only; the task pages use useTaskRecovery for retained IDs and unknown states.
export async function waitTask(projectId: string, taskId: string, signal?: AbortSignal): Promise<Models['Task']> {
  for (let i = 0; i < 120; i++) {
    if (signal?.aborted) throw new Error('页面已切换，停止等待')
    const task = await api.task(projectId, taskId)
    if (task.status === 'failed') throw taskError(task)
    if (task.status === 'succeeded') return task
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('等待超过 60 秒，请稍后查询任务状态')
}
