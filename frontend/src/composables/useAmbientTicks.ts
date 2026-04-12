/** Optional ambient tick sound for MiroFish simulation playback.
 *
 * Plays a very soft 80Hz click on each round advance (volume 0.04).
 * Default OFF — user discovers the toggle via the speaker glyph icon.
 * Persists preference to localStorage. Silently no-ops on failure
 * (headless/unsupported environments).
 */

import { ref, type Ref } from 'vue'

const STORAGE_KEY = 'mirofish.audio'

export interface AmbientTicks {
  readonly enabled: Ref<boolean>
  toggle(): void
  playTick(): void
}

export function useAmbientTicks(): AmbientTicks {
  const enabled = ref(false)
  let ctx: AudioContext | null = null

  // Restore preference from localStorage
  try {
    enabled.value = localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    // ignore
  }

  function _getContext(): AudioContext | null {
    if (ctx) return ctx
    try {
      ctx = new AudioContext()
      return ctx
    } catch {
      return null
    }
  }

  function toggle(): void {
    enabled.value = !enabled.value
    try {
      localStorage.setItem(STORAGE_KEY, String(enabled.value))
    } catch {
      // ignore
    }
  }

  function playTick(): void {
    if (!enabled.value) return
    const context = _getContext()
    if (!context) return

    try {
      const osc = context.createOscillator()
      const gain = context.createGain()
      osc.type = 'sine'
      osc.frequency.value = 80
      gain.gain.value = 0.04
      osc.connect(gain)
      gain.connect(context.destination)
      const now = context.currentTime
      osc.start(now)
      osc.stop(now + 0.04)
    } catch {
      // AudioContext may be suspended or unavailable
    }
  }

  return { enabled, toggle, playTick }
}
