/** Keyboard contract for MiroFish simulation visualization.
 *
 * Binds the Polanyi tacit keyboard contract:
 *   Space  — pause/resume global clock
 *   ←/→    — step one round back/forward
 *   Home   — jump to round 1
 *   End    — jump to final round
 *   F      — toggle focus mode
 *   R      — reset and replay from R1
 *   ?      — flash glyph overlay (1 second)
 *
 * Ignored when an <input>, <textarea>, or <select> element is focused.
 */

import { onMounted, onUnmounted, type Ref } from 'vue'
import type { Playback } from '@/composables/usePlayback'

export interface KeyboardShortcutOptions {
  totalRounds: Ref<number>
  focusToggle?: () => void
  glyphFlash?: () => void
}

export function useKeyboardShortcuts(
  playback: Playback,
  options: KeyboardShortcutOptions,
): void {
  function _isInputFocused(): boolean {
    const el = document.activeElement
    if (!el) return false
    const tag = el.tagName.toLowerCase()
    return tag === 'input' || tag === 'textarea' || tag === 'select'
  }

  function _onKeyDown(e: KeyboardEvent): void {
    if (_isInputFocused()) return

    switch (e.key) {
      case ' ':
        e.preventDefault()
        playback.toggle()
        break
      case 'ArrowRight':
        e.preventDefault()
        playback.step(1)
        break
      case 'ArrowLeft':
        e.preventDefault()
        playback.step(-1)
        break
      case 'Home':
        e.preventDefault()
        playback.seek(1)
        break
      case 'End':
        e.preventDefault()
        playback.seek(options.totalRounds.value)
        break
      case 'f':
      case 'F':
        options.focusToggle?.()
        break
      case 'r':
      case 'R':
        playback.reset()
        playback.play()
        break
      case '?':
        options.glyphFlash?.()
        break
    }
  }

  onMounted(() => {
    document.addEventListener('keydown', _onKeyDown)
  })

  onUnmounted(() => {
    document.removeEventListener('keydown', _onKeyDown)
  })
}
