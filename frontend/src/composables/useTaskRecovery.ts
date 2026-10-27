import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError, type Models } from '../api/client'
import { formatError, taskError } from '../api/errors'
import { user } from '../state'
import { pdfProblem } from '../validation'

type Kind = Models['Task']['kind']
type Phase = 'submitting' | 'unknown' | 'accepted' | 'rejected'
export type RecoveryRecord = {
  id: string; projectId: string; page: 'papers' | 'research'; kind: Kind
  key: string; fingerprint: string; phase: Phase; taskId?: string
  paperId?: string; paperIds: string[]
}
type Intent = { kind: Kind; question?: string; file?: File; title?: string; paperId?: string }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const kinds: Kind[] = ['process', 'answer', 'evidence', 'draft']
const safeError = (error: unknown) => error instanceof ApiError ? `${error.message}（${error.code}）` : '网络暂时不可用'
async function hash(value: BufferSource) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', value))
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

export function useTaskRecovery(projectId: string, page: RecoveryRecord['page'], onResult: (task: Models['Task']) => void | Promise<void>) {
  const ownerId = user.value?.user_id
  const storageKey = `paperpilot.tasks.v1.${ownerId}.${projectId}.${page}`
  const records = ref<RecoveryRecord[]>([]), selected = ref('')
  const task = ref<Models['Task'] | null>(null), busy = ref(false), message = ref(''), storageError = ref('')
  const queryUncertain = ref(false)
  const record = computed(() => records.value.find(item => item.id === selected.value))
  const unresolved = computed(() => records.value.find(item => item.phase === 'unknown' || item.phase === 'submitting'))
  const blocked = computed(() => busy.value || queryUncertain.value || !!storageError.value || !!unresolved.value || task.value?.status === 'pending' || task.value?.status === 'processing')
  const controller = new AbortController(), applied = new Set<string>()
  let disposed = false
  const active = () => !disposed && !!ownerId && user.value?.user_id === ownerId
  function persist() {
    if (!active()) throw new Error('Session changed')
    try { sessionStorage.setItem(storageKey, JSON.stringify(records.value)) }
    catch { storageError.value = '无法保存任务恢复记录，请保留已显示的任务编号；暂不接受新的提交。'; throw new Error('Recovery storage unavailable') }
  }
  function load() {
    try {
      if (!ownerId || !uuid.test(projectId)) throw new Error('Invalid scope')
      const raw: unknown = JSON.parse(sessionStorage.getItem(storageKey) || '[]')
      if (!Array.isArray(raw) || raw.length > 20) throw new Error('Invalid recovery record')
      records.value = raw.map((item: RecoveryRecord) => {
        if (!item || !uuid.test(item.id) || item.projectId !== projectId || item.page !== page || !kinds.includes(item.kind)
          || !uuid.test(item.key) || !/^[0-9a-f]{64}$/.test(item.fingerprint)
          || !['submitting', 'unknown', 'accepted', 'rejected'].includes(item.phase)
          || (item.taskId !== undefined && !uuid.test(item.taskId)) || (item.phase === 'accepted' && !item.taskId)
          || (item.paperId !== undefined && !uuid.test(item.paperId)) || !Array.isArray(item.paperIds)
          || item.paperIds.length > 50 || item.paperIds.some(id => typeof id !== 'string' || !uuid.test(id))) throw new Error('Invalid recovery record')
        // Project/user namespaces are hints only; the server authorizes every read.
        return { id: item.id, projectId, page, kind: item.kind, key: item.key, fingerprint: item.fingerprint,
          phase: item.phase === 'submitting' ? 'unknown' : item.phase, taskId: item.taskId, paperId: item.paperId, paperIds: item.paperIds }
      })
      selected.value = (unresolved.value || records.value[records.value.length - 1])?.id || ''
    } catch { storageError.value = '任务恢复记录不可用，未发起新任务；请使用已知任务编号或联系项目负责人核对。' }
  }
  load()
  onUnmounted(() => { disposed = true; controller.abort() })

  async function follow(item: RecoveryRecord) {
    if (!item.taskId) return
    queryUncertain.value = true
    const deadline = Date.now() + 60000
    while (active()) {
      if (Date.now() >= deadline) { message.value = '等待已超过 60 秒，当前状态未知；可重新查询同一任务。'; return }
      try {
        const current = await api.task(projectId, item.taskId, controller.signal)
        if (!active()) return
        if (current.task_id !== item.taskId || current.project_id !== projectId || current.kind !== item.kind) throw new ApiError('ADAPTER_CONTRACT_ERROR', '任务响应与恢复记录不一致')
        task.value = current
        if (current.status === 'failed') { queryUncertain.value = false; message.value = formatError(taskError(current)); return }
        if (current.status === 'succeeded') {
          queryUncertain.value = false
          message.value = '任务已完成，已读取已有结果。'
          const identity = `${current.task_id}:${current.attempts}:${current.updated_at}`
          if (!applied.has(identity)) {
            applied.add(identity)
            try { await onResult(current) }
            catch { applied.delete(identity); message.value = '任务已完成，但页面资料暂时无法加载；可重新查询已有结果。' }
          }
          return
        }
      } catch (error) {
        if (!active()) return
        if (error instanceof ApiError && error.status === 401) { task.value = null; message.value = '登录已失效，请重新登录后查询原任务。' }
        else if (error instanceof ApiError && (error.status === 403 || error.status === 404)) { task.value = null; message.value = '任务不存在或不可访问，未重新提交。' }
        else message.value = `当前任务状态未知：${safeError(error)}；可重新查询同一任务。`
        return
      }
      await new Promise(resolve => setTimeout(resolve, 500))
    }
  }
  async function refresh(id = selected.value) {
    if (busy.value || !active()) return
    const item = records.value.find(value => value.id === id)
    if (!item) return
    if (selected.value !== item.id) applied.clear()
    selected.value = item.id; task.value = null; message.value = ''
    if (!item.taskId) { message.value = '提交结果未知，尚未取得任务编号；不会自动重新提交。请恢复原输入后明确核对。'; return }
    busy.value = true
    try { await follow(item) } finally { busy.value = false }
  }
  onMounted(() => { if (!storageError.value) void refresh() })

  async function submit(intent: Intent, reconcile = false) {
    // Synchronous latch precedes hashing, file reads and all network operations.
    if (busy.value || !active() || storageError.value) return
    if (unresolved.value && !reconcile) { selected.value = unresolved.value.id; message.value = '提交结果未知，请恢复原输入并点击“使用原请求核对”；不会自动再次提交。'; return }
    if (queryUncertain.value && record.value?.taskId) { message.value = '已有任务的当前状态未知，请先重新查询原任务；未创建新任务。'; return }
    if (task.value && ['pending', 'processing'].includes(task.value.status)) { message.value = '已有任务尚未确认完成，请先重新查询。'; return }
    busy.value = true; message.value = ''
    let item: RecoveryRecord | undefined, sent = false
    try {
      const previous = reconcile ? unresolved.value : undefined
      if (reconcile && (!previous || previous.kind !== intent.kind)) throw new Error('请先恢复原请求对应的输入，再进行核对。')
      const paperIds = previous?.paperIds || ((intent.kind === 'answer' || intent.kind === 'draft')
        ? (await api.papers(projectId)).items.filter(p => p.status === 'searchable').map(p => p.paper_id).sort() : [])
      if (paperIds.length > 50) throw new Error('当前文献范围超过 50 篇，请先限定项目范围；未提交任务。')
      let payload: unknown
      if (intent.kind === 'process') {
        if (!intent.file || !intent.title?.trim()) throw new Error('请重新选择原 PDF 并填写原论文标题后核对。')
        const problem = await pdfProblem(intent.file)
        if (problem) throw new Error(problem)
        payload = { kind: intent.kind, title: intent.title, digest: await hash(await intent.file.arrayBuffer()) }
      } else if (intent.kind === 'evidence') {
        if (!intent.paperId) throw new Error('缺少原论文编号，未提交。')
        payload = { kind: intent.kind, paperId: intent.paperId }
      } else {
        if (!intent.question?.trim()) throw new Error('请填写原研究问题后核对。')
        payload = { kind: intent.kind, question: intent.question, paperIds }
      }
      const fingerprint = await hash(new TextEncoder().encode(JSON.stringify(payload)))
      if (!active()) return
      if (previous && (previous.fingerprint !== fingerprint || previous.paperId !== intent.paperId)) throw new Error('当前输入与原请求不一致，未发送；请恢复原始输入或联系项目负责人核对。')
      const accepted = records.value.find(value => value.phase === 'accepted' && value.kind === intent.kind && value.fingerprint === fingerprint)
      if (!previous && accepted) { selected.value = accepted.id; task.value = null; await follow(accepted); return }
      item = previous || { id: crypto.randomUUID(), projectId, page, kind: intent.kind, key: crypto.randomUUID(), fingerprint, phase: 'submitting', paperId: intent.paperId, paperIds }
      if (!previous) { records.value = [...records.value.slice(-19), item]; item = records.value[records.value.length - 1]! }
      selected.value = item.id; task.value = null; item.phase = 'submitting'
      persist() // A request cannot start if its recovery record cannot be saved.
      sent = true
      const current = intent.kind === 'process' ? (await api.upload(projectId, intent.file!, intent.title!, item.key)).task
        : intent.kind === 'answer' ? await api.answer(projectId, intent.question!, item.key, paperIds)
        : intent.kind === 'draft' ? await api.draft(projectId, intent.question!, item.key, paperIds)
        : await api.evidence(projectId, intent.paperId!, item.key)
      if (!active()) return
      if (!uuid.test(current.task_id) || current.project_id !== projectId || current.kind !== intent.kind) throw new Error('Invalid task acknowledgement')
      item.taskId = current.task_id; item.phase = 'accepted'; task.value = current
      try { persist() } catch { /* Keep the acknowledged ID in memory; never resubmit. */ }
      await follow(item)
    } catch (error) {
      if (!active()) return
      if (item && sent && !item.taskId) {
        // Only a known rejection of the first attempt can resolve an unacknowledged request.
        const rejected = !reconcile && error instanceof ApiError && (
          [400, 401, 403, 404, 409, 413, 415, 422].includes(error.status || 0)
          || ['DATA_UNAVAILABLE', 'FILE_STORAGE_UNAVAILABLE', 'AUDIT_UNAVAILABLE'].includes(error.code))
        item.phase = rejected ? 'rejected' : 'unknown'
        try { persist() } catch { /* A pre-request record already exists when storage remains available. */ }
        message.value = rejected ? `请求已被拒绝：${safeError(error)}` : `提交结果未知，未自动重新提交。${error instanceof ApiError ? safeError(error) : '连接或确认响应不可用'}。请使用原请求核对。`
      } else message.value = error instanceof Error ? error.message : '无法准备任务请求，未提交。'
    } finally { busy.value = false }
  }
  async function retryFailed() {
    const item = record.value
    if (busy.value || !active() || !item?.taskId || task.value?.status !== 'failed') return
    busy.value = true; message.value = ''
    queryUncertain.value = true
    try {
      // Retry has no creation idempotency key. Check current state first, then retain the same ID.
      const current = await api.task(projectId, item.taskId, controller.signal)
      if (!active()) return
      if (current.status === 'failed') await api.retry(projectId, item.taskId)
      await follow(item)
    } catch { if (active()) message.value = '重试或查询的结果暂不明确，请重新查询原任务；未创建新任务。' }
    finally { busy.value = false }
  }
  return { records, record, selected, task, busy, blocked, message, storageError, submit, refresh, retryFailed }
}
