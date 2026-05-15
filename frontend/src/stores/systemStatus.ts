/** Pinia store wrapping the five freeze-source snapshot polled by AppShell. */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { systemStatusApi } from '@/api/systemStatus'
import {
  FREEZE_SOURCE_NAMES,
  type FreezeSource,
  type FreezeSourceName,
  type FreezeSourcesPayload,
} from '@/types/systemStatus'

const FALLBACK_TIMESTAMP = ''

function _defaultSources(): FreezeSource[] {
  return FREEZE_SOURCE_NAMES.map((name) => {
    switch (name) {
      case 'mode_switch':
        return { name, active: false, status: 'unavailable', reason: null, context: null }
      case 'reconciliation_ticket':
        return { name, active: false, status: 'unavailable', reason: null, ticket_id: null }
      case 'circuit_breaker':
        return { name, active: false, status: 'unavailable', reason: null, halted_at: null, consecutive_losses: null }
      case 'data_quality':
        return { name, active: false, status: 'unavailable', reason: null, code: null }
      case 'eod_pipeline':
        return { name, active: false, status: 'unavailable', reason: null, raised_at: null, trade_date: null }
    }
  })
}

export const useSystemStatusStore = defineStore('systemStatus', () => {
  const sources = ref<readonly FreezeSource[]>(_defaultSources())
  const anyActive = ref(false)
  const anyUnavailable = ref(true)
  const timestamp = ref(FALLBACK_TIMESTAMP)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchFreezeSources(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const payload: FreezeSourcesPayload = await systemStatusApi.getFreezeSources()
      sources.value = payload.sources
      anyActive.value = payload.any_active
      anyUnavailable.value = payload.any_unavailable
      timestamp.value = payload.timestamp
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : 'failed to load freeze sources'
    } finally {
      loading.value = false
    }
  }

  function sourceByName(name: FreezeSourceName): FreezeSource {
    const match = sources.value.find((s) => s.name === name)
    if (!match) {
      // Should never happen — backend contract test enforces the five names.
      return _defaultSources().find((s) => s.name === name)!
    }
    return match
  }

  return {
    sources,
    anyActive,
    anyUnavailable,
    timestamp,
    loading,
    error,
    fetchFreezeSources,
    sourceByName,
  }
})
