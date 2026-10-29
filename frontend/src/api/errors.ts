export class ApiError extends Error {
  constructor(public code: string, message: string, public requestId?: string, public status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

const messages: Record<string, string> = {
  UNAUTHENTICATED: '请重新登录后继续操作',
  INVALID_IDENTITY: '开发演示账户不存在',
  INVALID_CREDENTIALS: '账户名或密码不正确',
  LOGIN_RATE_LIMIT: '登录尝试过多，请稍后重试',
  AUTH_BUSY: '登录繁忙，请稍后重试',
  HTTPS_REQUIRED: '密码登录需要使用 HTTPS 地址',
  PASSWORD_AUTH_DISABLED: '当前为演示登录，请勿提交真实密码',
  OWNER_REQUIRED: '此操作需要项目负责人权限',
  RESOURCE_NOT_FOUND: '资源不存在或不可访问',
  VALIDATION_ERROR: '请求字段或筛选条件不正确，请检查后重试',
  DUPLICATE_PAPER: '项目中已有相同文件，请查看已有文献',
  SOURCE_IMPACT_CONFIRMATION_REQUIRED: '移出后不再参与检索，历史报告保留引用快照；请先确认影响',
  IDEMPOTENCY_CONFLICT: '请求内容与原请求不一致，请恢复原输入后核对',
  DATA_COMMIT_UNKNOWN: '提交结果未知，请使用原请求核对，不要重复创建',
  DATA_UNAVAILABLE: '数据服务暂不可用，请稍后重新查询',
  TASK_QUERY_UNAVAILABLE: '任务状态暂不可查询，请保留原任务编号后重查',
  FILE_STORAGE_UNAVAILABLE: '文件保存失败，尚未提交论文，请稍后重试',
  AUDIT_UNAVAILABLE: '审计服务暂不可用，未返回操作结果；请先核对状态',
  AI_UNAVAILABLE: 'AI 服务暂不可用，请稍后重试',
  GATEWAY_UNAVAILABLE: '安全网关暂不可用，未返回查询结果',
  ADAPTER_CONTRACT_ERROR: '模块响应不符合契约，请保留请求编号反馈问题',
  INVALID_CITATION: '来源校验未通过，请重新查询并核对引用',
  UPLOAD_TOO_LARGE: '单篇文件上限为 20 MiB',
  UNSUPPORTED_FILE: '文件格式不受支持，请使用文本型 PDF',
  UNSUPPORTED_QUERY: '当前查询不在网关支持范围内',
  MOCK_EXECUTION_FAILED: '模拟查询执行失败，未返回结果',
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function responseRequestId(response: Response, payload?: unknown): string | undefined {
  const value = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const candidates = [value.request_id, response.headers.get('X-Request-ID')]
  return candidates.find(id => typeof id === 'string' && uuid.test(id)) as string | undefined
}

export function httpError(response: Response, payload: unknown): ApiError {
  const value = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const code = typeof value.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value.code) ? value.code : 'REQUEST_FAILED'
  const requestId = responseRequestId(response, payload)
  const fallback = response.status === 401 ? '请重新登录后继续操作'
    : response.status === 403 ? '没有执行此操作的权限'
    : response.status === 404 ? '资源不存在或不可访问'
    : response.status === 409 ? '操作发生冲突，请先重新查询当前状态'
    : response.status >= 500 ? '服务暂时无法处理请求，请稍后重新查询'
    : '请求未被接受，请检查输入后重试'
  // Provider messages and non-JSON proxy bodies are not trusted display text.
  return new ApiError(code, messages[code] || fallback, requestId, response.status)
}

export function normalizeError(error: unknown): never {
  if (error instanceof ApiError) throw error
  throw new ApiError('INVALID_RESPONSE', '服务响应格式不正确，请保留当前输入后重新查询')
}

export function formatError(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}（${error.code}${error.requestId ? ` · ${error.requestId}` : ''}）`
  return '操作未完成，请稍后重试；未展示内部错误详情'
}

export function formatQueryReason(reason: { code: string; message: string }): string {
  const code = /^[A-Z][A-Z0-9_]{0,63}$/.test(reason.code) ? reason.code : 'QUERY_NOT_EXECUTED'
  return `${messages[code] || '查询未完成或被网关拒绝，请按请求编号核对'}（${code}）`
}

export function taskError(task: { error?: { code: string; message: string } | null; request_id: string }): ApiError {
  const code = task.error && /^[A-Z][A-Z0-9_]{0,63}$/.test(task.error.code) ? task.error.code : 'TASK_FAILED'
  return new ApiError(code, messages[code] || '任务处理失败，请按请求编号核对原因', uuid.test(task.request_id) ? task.request_id : undefined)
}
