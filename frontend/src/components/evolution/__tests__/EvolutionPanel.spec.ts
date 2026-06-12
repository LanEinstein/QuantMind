import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import EvolutionPanel from '@/components/evolution/EvolutionPanel.vue'
import { evolutionApi } from '@/api/evolution'
import type { EvolutionHistoryPayload } from '@/types/evolution'

vi.mock('@/api/evolution', () => ({
  evolutionApi: { getHistory: vi.fn() },
}))

const historyMock = vi.mocked(evolutionApi.getHistory)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  ElTable: {
    props: ['data'],
    template: '<div class="el-table" :data-rows="data ? data.length : 0"><slot /></div>',
  },
  ElTableColumn: { template: '<span />' },
}

function payload(over: Partial<EvolutionHistoryPayload> = {}): EvolutionHistoryPayload {
  return {
    experiments: [],
    intents: [],
    current_manifest: {
      version: '1.0',
      updated_at: '2026-06-11T00:00:00+00:00',
      approved: { prompt_version: ['a'.repeat(64)], strategy_code: [] },
    },
    source: 'mongo',
    timestamp: '2026-06-12T08:00:00+00:00',
    ...over,
  }
}

let wrapper: VueWrapper | null = null

async function mountPanel() {
  wrapper = mount(EvolutionPanel, { global: { stubs: globalStubs } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => historyMock.mockReset())
afterEach(() => {
  // Unmount so a prior test's still-mounted component cannot leave a pending
  // reactive effect that vitest mis-attributes to the next test.
  wrapper?.unmount()
  wrapper = null
})

describe('EvolutionPanel (AD-003)', () => {
  it('renders the current manifest approved set', async () => {
    historyMock.mockResolvedValue(payload())
    const wrapper = await mountPanel()
    expect(wrapper.text()).toContain('version 1.0')
    expect(wrapper.text()).toContain('prompt_version')
  })

  it('shows failed experiments (not only winners)', async () => {
    historyMock.mockResolvedValue(
      payload({
        experiments: [
          {
            experiment_id: 'e1',
            kind: 'THRESHOLD_PARAM',
            family: 'fam',
            hypothesis: 'h',
            success: false,
            trading_days: 20,
            sample_count: 30,
            metrics: {},
            registered_at: '2026-06-10T00:00:00+00:00',
          },
        ],
      }),
    )
    const wrapper = await mountPanel()
    // The experiments table got the 1 failed row.
    const tables = wrapper.findAll('.el-table')
    expect(tables.some((t) => t.attributes('data-rows') === '1')).toBe(true)
  })

  it('renders the unwired/empty state when Mongo is unavailable', async () => {
    historyMock.mockResolvedValue(
      payload({
        source: 'unavailable',
        experiments: [],
        intents: [],
      }),
    )
    const wrapper = await mountPanel()
    expect(wrapper.text()).toContain('实验注册表未接线')
    // Manifest still shown (read from the lockfile, independent of Mongo).
    expect(wrapper.text()).toContain('version 1.0')
  })
})
