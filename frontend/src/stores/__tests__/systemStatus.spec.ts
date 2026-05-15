/**
 * Tests for the systemStatus pinia store mirror of P1-5 §1.1 schema.
 *
 * Locks the contract that the five freeze-source names match the
 * backend FREEZE_SOURCE_NAMES tuple and that ``sourceByName`` never
 * crashes when a probe is unwired (returns a synthetic unavailable
 * record so the StatusBar UI stays renderable).
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSystemStatusStore } from '@/stores/systemStatus'
import {
  FREEZE_SOURCE_NAMES,
  type FreezeSourcesPayload,
} from '@/types/systemStatus'

vi.mock('@/api/systemStatus', () => ({
  systemStatusApi: {
    getFreezeSources: vi.fn(),
  },
}))

import { systemStatusApi } from '@/api/systemStatus'

const mockedApi = systemStatusApi as unknown as {
  getFreezeSources: ReturnType<typeof vi.fn>
}

const _allActivePayload: FreezeSourcesPayload = {
  sources: [
    { name: 'mode_switch', active: true, status: 'ok', reason: 'switching', context: null },
    { name: 'reconciliation_ticket', active: true, status: 'ok', reason: 'cash_diff', ticket_id: 'RECON-20260515-001' },
    { name: 'circuit_breaker', active: true, status: 'ok', reason: 'daily_loss', halted_at: null, consecutive_losses: 3 },
    { name: 'data_quality', active: true, status: 'ok', reason: 'stale', code: '600519' },
    { name: 'eod_pipeline', active: true, status: 'ok', reason: 'checksum', raised_at: null, trade_date: '20260515' },
  ],
  any_active: true,
  any_unavailable: false,
  timestamp: '2026-05-15T16:00:00+00:00',
}

const _allUnavailablePayload: FreezeSourcesPayload = {
  sources: FREEZE_SOURCE_NAMES.map((name) => {
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
  }),
  any_active: false,
  any_unavailable: true,
  timestamp: '2026-05-15T16:00:00+00:00',
}

describe('useSystemStatusStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockedApi.getFreezeSources.mockReset()
  })

  it('seeds the store with five unavailable sources before any fetch', () => {
    const store = useSystemStatusStore()
    expect(store.sources.map((s) => s.name)).toEqual(Array.from(FREEZE_SOURCE_NAMES))
    for (const source of store.sources) {
      expect(source.status).toBe('unavailable')
      expect(source.active).toBe(false)
    }
    expect(store.anyActive).toBe(false)
    expect(store.anyUnavailable).toBe(true)
  })

  it('hydrates from the API on fetchFreezeSources()', async () => {
    mockedApi.getFreezeSources.mockResolvedValueOnce(_allActivePayload)
    const store = useSystemStatusStore()

    await store.fetchFreezeSources()

    expect(store.sources.map((s) => s.name)).toEqual(Array.from(FREEZE_SOURCE_NAMES))
    expect(store.anyActive).toBe(true)
    expect(store.anyUnavailable).toBe(false)
    expect(store.timestamp).toBe('2026-05-15T16:00:00+00:00')
    expect(store.error).toBeNull()
  })

  it('captures errors without crashing when the API rejects', async () => {
    mockedApi.getFreezeSources.mockRejectedValueOnce(new Error('boom'))
    const store = useSystemStatusStore()

    await store.fetchFreezeSources()

    expect(store.error).toBe('boom')
    expect(store.loading).toBe(false)
  })

  it('sourceByName returns the freeze source for each locked name', async () => {
    mockedApi.getFreezeSources.mockResolvedValueOnce(_allActivePayload)
    const store = useSystemStatusStore()
    await store.fetchFreezeSources()

    for (const name of FREEZE_SOURCE_NAMES) {
      const source = store.sourceByName(name)
      expect(source.name).toBe(name)
    }
  })

  it('preserves five-source order on hydration (P1-5 §2 redline 4)', async () => {
    mockedApi.getFreezeSources.mockResolvedValueOnce(_allUnavailablePayload)
    const store = useSystemStatusStore()
    await store.fetchFreezeSources()

    expect(store.sources.map((s) => s.name)).toEqual(Array.from(FREEZE_SOURCE_NAMES))
  })
})
