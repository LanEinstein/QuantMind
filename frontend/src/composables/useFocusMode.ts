/** Focus mode composable — collapses sidebar/header to 36px hairlines.
 *
 * Sets/removes the `focus-mode` class on document.body, which CSS
 * uses to transition sidebar and header into minimal visibility.
 * Persists to localStorage so the preference survives page reloads.
 */

import { ref, onMounted, onUnmounted, type Ref } from 'vue'

const STORAGE_KEY = 'mirofish.focusMode'

export interface FocusMode {
  readonly active: Ref<boolean>
  toggle(): void
}

export function useFocusMode(): FocusMode {
  const active = ref(false)

  function _applyToDOM(value: boolean): void {
    if (value) {
      document.body.classList.add('focus-mode')
    } else {
      document.body.classList.remove('focus-mode')
    }
  }

  function toggle(): void {
    active.value = !active.value
    _applyToDOM(active.value)
    try {
      localStorage.setItem(STORAGE_KEY, String(active.value))
    } catch {
      // localStorage may be unavailable in some contexts
    }
  }

  onMounted(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored === 'true') {
        active.value = true
        _applyToDOM(true)
      }
    } catch {
      // ignore
    }
  })

  onUnmounted(() => {
    // Restore body class on unmount to avoid stale class when navigating away
    document.body.classList.remove('focus-mode')
  })

  return { active, toggle }
}
