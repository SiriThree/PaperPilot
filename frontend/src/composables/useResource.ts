import { onScopeDispose, ref, shallowRef } from 'vue'
import { formatError } from '../api/errors'

// Read-only resources have their own state; a failed refresh is never an empty result.
export function useResource<T>(initial: T, read: () => Promise<T>) {
  const data = shallowRef<T>(initial), loading = ref(false), loaded = ref(false), error = ref('')
  let sequence = 0, disposed = false
  function reset() {
    sequence++; data.value = initial; loading.value = false; loaded.value = false; error.value = ''
  }
  async function load(): Promise<boolean> {
    const current = ++sequence
    loading.value = true; loaded.value = false; error.value = ''; data.value = initial
    try {
      const value = await read()
      if (disposed || current !== sequence) return false
      data.value = value; loaded.value = true
      return true
    } catch (failure) {
      if (!disposed && current === sequence) error.value = formatError(failure)
      return false
    } finally { if (!disposed && current === sequence) loading.value = false }
  }
  onScopeDispose(() => { disposed = true; sequence++ })
  return { data, loading, loaded, error, load, reset }
}
