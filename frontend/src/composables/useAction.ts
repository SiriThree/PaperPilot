import { ref } from 'vue'
import { ApiError } from '../api/client'

export function useAction() {
  const busy = ref(false), error = ref('')
  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    if (busy.value) return undefined
    busy.value = true; error.value = ''
    try { return await action() }
    catch (e) { error.value = e instanceof ApiError ? `${e.message}（${e.code} · ${e.requestId || '无请求编号'}）` : e instanceof Error ? e.message : '网络请求失败' }
    finally { busy.value = false }
  }
  return { busy, error, run }
}
