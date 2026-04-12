/** Global playback clock for MiroFish simulation visualization.
 *
 * Single instance created in Simulation.vue, provided via PLAYBACK_KEY
 * so all four chart zones share one source of truth for the current round.
 */

import { ref, onUnmounted, type Ref, type InjectionKey } from 'vue'

export interface Playback {
  readonly currentRound: Ref<number>
  readonly isPlaying: Ref<boolean>
  play(): void
  pause(): void
  toggle(): void
  step(delta: number): void
  seek(round: number): void
  reset(): void
}

export function usePlayback(totalRounds: Ref<number>, intervalMs = 400): Playback {
  const currentRound = ref(1)
  const isPlaying = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  function _stop(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    isPlaying.value = false
  }

  function play(): void {
    if (isPlaying.value) return
    if (currentRound.value >= totalRounds.value) {
      currentRound.value = 1
    }
    isPlaying.value = true
    timer = setInterval(() => {
      if (currentRound.value >= totalRounds.value) {
        _stop()
        return
      }
      currentRound.value += 1
    }, intervalMs)
  }

  function pause(): void {
    _stop()
  }

  function toggle(): void {
    if (isPlaying.value) {
      pause()
    } else {
      play()
    }
  }

  function step(delta: number): void {
    pause()
    const next = currentRound.value + delta
    currentRound.value = Math.max(1, Math.min(totalRounds.value, next))
  }

  function seek(round: number): void {
    pause()
    currentRound.value = Math.max(1, Math.min(totalRounds.value, round))
  }

  function reset(): void {
    pause()
    currentRound.value = 1
  }

  onUnmounted(_stop)

  return { currentRound, isPlaying, play, pause, toggle, step, seek, reset }
}

export const PLAYBACK_KEY = Symbol('playback') as InjectionKey<Playback>
