import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SlotRotationPanel from '@/components/portfolio/SlotRotationPanel.vue'
import { slotRotationApi } from '@/api/slotRotation'
import type { SlotRotationPayload } from '@/types/slotRotation'

vi.mock('@/api/slotRotation', () => ({
  slotRotationApi: { get: vi.fn() },
}))

const getMock = vi.mocked(slotRotationApi.get)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  ElTable: {
    props: ['data'],
    template: '<div class="el-table" :data-rows="data ? data.length : 0"><slot /></div>',
  },
  ElTableColumn: { template: '<span />' },
}

function payload(over: Partial<SlotRotationPayload> = {}): SlotRotationPayload {
  return {
    available: true,
    note: '',
    max_total_positions: 5,
    underinvested_block_active: false,
    open_intent_count: 0,
    open_intents: [],
    recent_events: [],
    ...over,
  }
}

async function mountPanel(heldCount = 0) {
  const wrapper = mount(SlotRotationPanel, {
    props: { heldCount },
    global: { stubs: globalStubs },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  getMock.mockReset()
})

describe('SlotRotationPanel', () => {
  it('shows the unwired note when the runner is unavailable', async () => {
    getMock.mockResolvedValue(payload({ available: false, note: '未接线' }))
    const wrapper = await mountPanel()
    expect(wrapper.find('.placeholder-text').text()).toContain('未接线')
    expect(wrapper.find('.slot-strip').exists()).toBe(false)
  })

  it('renders one slot cell per cap, filled up to heldCount', async () => {
    getMock.mockResolvedValue(payload({ max_total_positions: 5 }))
    const wrapper = await mountPanel(3)
    expect(wrapper.findAll('.slot-cell')).toHaveLength(5)
    expect(wrapper.findAll('.slot-cell.filled')).toHaveLength(3)
    expect(wrapper.findAll('.slot-cell.empty')).toHaveLength(2)
    expect(wrapper.find('.slot-strip-label').text()).toBe('3/5 槽占用')
  })

  it('shows the underinvested block banner when active', async () => {
    getMock.mockResolvedValue(payload({ underinvested_block_active: true }))
    const wrapper = await mountPanel()
    expect(wrapper.find('.banner-warn').exists()).toBe(true)
  })

  it('passes open intents + recent events into their tables', async () => {
    getMock.mockResolvedValue(
      payload({
        open_intent_count: 1,
        open_intents: [
          {
            intent_id: 'ROT-20260612-600000-000002',
            created_trade_date: '20260612',
            expires_at_trade_date: '20260615',
            sell_instruction_id: 'QM-20260612-093500-000001-SELL-001',
            incumbent_code: '600000',
            challenger_code: '000002',
            incumbent_score: 0.31,
            challenger_score: 0.88,
            incumbent_percentile: 0.35,
            challenger_percentile: 0.92,
          },
        ],
        recent_events: [
          {
            event_type: 'proposed',
            trade_date: '20260612',
            intent_id: 'ROT-20260612-600000-000002',
            incumbent_code: '600000',
            challenger_code: '000002',
            outcome_kind: null,
            buy_code: null,
            blocks_further_rotation: false,
            note: 'rotation SELL issued',
          },
        ],
      }),
    )
    const wrapper = await mountPanel(5)
    const tables = wrapper.findAll('.el-table')
    expect(tables[0].attributes('data-rows')).toBe('1')
    expect(tables[1].attributes('data-rows')).toBe('1')
  })

  it('surfaces a load error', async () => {
    getMock.mockRejectedValue(new Error('boom'))
    const wrapper = await mountPanel()
    expect(wrapper.find('.error-text').text()).toContain('boom')
  })
})
