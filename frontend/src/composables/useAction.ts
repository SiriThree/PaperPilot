import { ref } from 'vue'
import { formatError } from '../api/errors'

export function useAction() {
  const busy = ref(false), error = ref('')
  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    if (busy.value) return undefined
    busy.value = true; error.value = ''
    try { return await action() }
    catch (e) { error.value = formatError(e) }
    finally { busy.value = false }
  }
  return { busy, error, run }
}
