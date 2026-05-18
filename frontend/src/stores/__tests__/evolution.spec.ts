/**
 * X-023 — evolution pinia store unit tests.
 *
 * Locks the four colour-band transitions the SystemStatus.vue card
 * relies on (green / yellow / red / unavailable) plus the network
 * failure → ``unavailable`` regression: the card must NEVER stay
 * green when the polling fetch fails (mirrors systemStatus store
 * codex cycle 1 P2).
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useEvolutionStore } from '@/stores/evolution'
import type { EvolutionPendingPayload } from '@/types/evolution'

vi.mock('@/api/evolution', () => ({
  evolutionApi: {
    getPending: vi.fn(),
  },
}))

import { evolutionApi } from '@/api/evolution'

const mockedApi = evolutionApi as unknown as {
  getPending: ReturnType<typeof vi.fn>
}

function _payload(count: number): EvolutionPendingPayload {
  return {
    pending_dir: 'docs/decisions/pending',
    count,
    yellow_threshold: 1,
    red_threshold: 4,
    items: Array.from({ length: count }, (_, i) => ({
      amendment_id: `AMD-${i + 1}`,
      filename: `AMD-${i + 1}.md`,
      mtime: '2026-05-18T22:00:00+00:00',
      size_bytes: 1024,
    })),
    timestamp: '2026-05-18T22:00:00+00:00',
  }
}

describe('useEvolutionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockedApi.getPending.mockReset()
  })

  it('starts in the unavailable state before any fetch', () => {
    const store = useEvolutionStore()
    expect(store.status).toBe('unavailable')
    expect(store.probeAvailable).toBe(false)
    expect(store.count).toBe(0)
  })

  it('reports green when count=0', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(0))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.count).toBe(0)
    expect(store.status).toBe('green')
    expect(store.probeAvailable).toBe(true)
  })

  it('reports yellow when count=1 (lower bound of yellow band)', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(1))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.status).toBe('yellow')
  })

  it('reports yellow when count=3 (top of yellow band before red threshold=4)', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(3))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.status).toBe('yellow')
  })

  it('flips to red when count=red_threshold (=4)', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(4))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.status).toBe('red')
  })

  it('reports red when count exceeds the red threshold', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(7))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.status).toBe('red')
    expect(store.count).toBe(7)
    expect(store.items.length).toBe(7)
  })

  it('captures error and degrades probeAvailable when the API rejects', async () => {
    mockedApi.getPending.mockRejectedValueOnce(new Error('boom'))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.error).toBe('boom')
    expect(store.probeAvailable).toBe(false)
    expect(store.status).toBe('unavailable')
    expect(store.loading).toBe(false)
  })

  it('on failure-after-success the card degrades to unavailable (never stays green)', async () => {
    // First fetch — successful, green
    mockedApi.getPending.mockResolvedValueOnce(_payload(0))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.status).toBe('green')

    // Second fetch — backend outage
    mockedApi.getPending.mockRejectedValueOnce(new Error('network down'))
    await store.fetchPending()

    // Critical: must not stay green on a failed poll.
    expect(store.status).toBe('unavailable')
    expect(store.probeAvailable).toBe(false)
  })

  it('honours backend threshold drift (e.g. amendment lowers red_threshold to 3)', async () => {
    mockedApi.getPending.mockResolvedValueOnce({
      ..._payload(3),
      yellow_threshold: 1,
      red_threshold: 3,
    })
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.redThreshold).toBe(3)
    expect(store.status).toBe('red')
  })

  it('exposes the pending_dir so the operator can see the substrate path', async () => {
    mockedApi.getPending.mockResolvedValueOnce(_payload(2))
    const store = useEvolutionStore()
    await store.fetchPending()
    expect(store.pendingDir).toBe('docs/decisions/pending')
  })
})
