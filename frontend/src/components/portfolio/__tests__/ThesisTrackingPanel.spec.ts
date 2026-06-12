import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ThesisTrackingPanel from '@/components/portfolio/ThesisTrackingPanel.vue'
import { positionThesesApi } from '@/api/positionTheses'
import type { PositionThesesPayload } from '@/types/positionThesis'

vi.mock('@/api/positionTheses', () => ({
  positionThesesApi: { list: vi.fn() },
}))

const listMock = vi.mocked(positionThesesApi.list)

const ADVISORY = { note: 'advisory display-only' }

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  ElTable: {
    props: ['data'],
    template: '<div class="el-table" :data-rows="data ? data.length : 0"><slot /></div>',
  },
  ElTableColumn: { template: '<span />' },
}

function payload(
  over: Partial<PositionThesesPayload> = {},
): PositionThesesPayload {
  return {
    available: true,
    note: '',
    thesis_count: 0,
    theses: [],
    advisory: ADVISORY,
    ...over,
  }
}

async function mountPanel() {
  const wrapper = mount(ThesisTrackingPanel, { global: { stubs: globalStubs } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  listMock.mockReset()
})

describe('ThesisTrackingPanel', () => {
  it('shows the unwired note when the store is unavailable', async () => {
    listMock.mockResolvedValue(payload({ available: false, note: '未接线' }))
    const wrapper = await mountPanel()
    expect(wrapper.find('.placeholder-text').text()).toContain('未接线')
    expect(wrapper.find('.thesis-card').exists()).toBe(false)
  })

  it('shows an empty message when wired but no theses', async () => {
    listMock.mockResolvedValue(payload({ available: true }))
    const wrapper = await mountPanel()
    expect(wrapper.find('.placeholder-text').text()).toContain('暂无在持 thesis')
  })

  it('renders pillars + invalidation thresholds for an open thesis', async () => {
    listMock.mockResolvedValue(
      payload({
        thesis_count: 1,
        theses: [
          {
            stock_code: '600519',
            stock_name: '贵州茅台',
            instruction_id: 'QM-20260612-093500-000001-BUY-001',
            trade_date: '2026-06-12',
            created_at: '2026-06-12T09:35:00+00:00',
            entry_price: 1700,
            entry_score: 0.82,
            time_stop_trade_days: 20,
            catalyst_window_end: null,
            pillars: ['龙头护城河', '盈利稳健'],
            invalidation_conditions: [
              {
                template: 'anchor_drawdown',
                metric_name: 'price',
                comparator: 'lt',
                threshold: 1530,
                anchor: 1700,
              },
            ],
            evidence_ids: [],
          },
        ],
      }),
    )
    const wrapper = await mountPanel()
    const cards = wrapper.findAll('.thesis-card')
    expect(cards).toHaveLength(1)
    const pillars = wrapper.findAll('.pillar-list li').map((li) => li.text())
    expect(pillars).toEqual(['龙头护城河', '盈利稳健'])
    // The invalidation-condition table received the 1 deterministic threshold.
    expect(wrapper.find('.el-table').attributes('data-rows')).toBe('1')
  })

  it('surfaces a load error', async () => {
    listMock.mockRejectedValue(new Error('boom'))
    const wrapper = await mountPanel()
    expect(wrapper.find('.error-text').text()).toContain('boom')
  })
})
