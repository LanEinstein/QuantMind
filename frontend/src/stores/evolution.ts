/**
 * X-023 — Pinia store for the SystemStatus.vue self-evolution card.
 *
 * Polls ``GET /api/evolution/pending`` every five minutes (per X-023
 * spec) and exposes a derived ``status`` computed property the view
 * can use to switch between the four colour bands
 * (``green / yellow / red / unavailable``).
 *
 * No localStorage / sessionStorage / cookie writes — the store is
 * purely in-memory (P1-5 §2 红线 14: front-end stores no credentials).
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { evolutionApi } from '@/api/evolution'
import {
  evolutionPendingStatus,
  type EvolutionPendingItem,
  type EvolutionPendingPayload,
  type EvolutionPendingStatus,
} from '@/types/evolution'

// Default polling cadence — matches the X-023 spec (5 min). Exposed
// as a const so the view component can read the same value when
// installing its setInterval.
export const EVOLUTION_POLL_INTERVAL_MS = 5 * 60 * 1000

// Default thresholds mirroring the backend X-021 lock — the *real*
// thresholds always come from the most recent payload; these defaults
// only matter for the brief moment before the first fetch completes.
const DEFAULT_YELLOW_THRESHOLD = 1
const DEFAULT_RED_THRESHOLD = 4

export const useEvolutionStore = defineStore('evolution', () => {
  const count = ref(0)
  const items = ref<readonly EvolutionPendingItem[]>([])
  const yellowThreshold = ref(DEFAULT_YELLOW_THRESHOLD)
  const redThreshold = ref(DEFAULT_RED_THRESHOLD)
  const pendingDir = ref<string | null>(null)
  const timestamp = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // ``unavailable`` until we successfully hydrate at least once — the
  // X-023 card renders grey while the probe is still cold so an
  // operator does not mis-read "all green" before any data has arrived.
  const probeAvailable = ref(false)

  const status = computed<EvolutionPendingStatus>(() => {
    if (!probeAvailable.value) {
      return 'unavailable'
    }
    return evolutionPendingStatus(
      count.value,
      yellowThreshold.value,
      redThreshold.value,
    )
  })

  async function fetchPending(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const payload: EvolutionPendingPayload = await evolutionApi.getPending()
      count.value = payload.count
      items.value = payload.items
      yellowThreshold.value = payload.yellow_threshold
      redThreshold.value = payload.red_threshold
      pendingDir.value = payload.pending_dir
      timestamp.value = payload.timestamp
      probeAvailable.value = true
    } catch (err: unknown) {
      error.value =
        err instanceof Error ? err.message : 'failed to load evolution/pending'
      // The probe degrades to "unavailable" on failure so the operator
      // never reads the stale last-known-good as a live signal — same
      // failure-mode as the systemStatus store.
      probeAvailable.value = false
    } finally {
      loading.value = false
    }
  }

  return {
    count,
    items,
    yellowThreshold,
    redThreshold,
    pendingDir,
    timestamp,
    loading,
    error,
    probeAvailable,
    status,
    fetchPending,
  }
})
