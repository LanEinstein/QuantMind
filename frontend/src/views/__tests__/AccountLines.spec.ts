import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AccountLines from '@/views/AccountLines.vue'
import { accountLinesApi, type AccountLinesPayload } from '@/api/accountLines'

vi.mock('@/api/accountLines', () => ({
  accountLinesApi: { get: vi.fn() },
}))

const getMock = vi.mocked(accountLinesApi.get)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button class="el-button"><slot /></button>' },
  ElTable: {
    props: ['data'],
    template: '<div class="el-table" :data-rows="data ? data.length : 0"><slot /></div>',
  },
  ElTableColumn: { template: '<span />' },
}

function payload(over: Partial<AccountLinesPayload> = {}): AccountLinesPayload {
  return {
    r_line: { positions: [], cash: 150000, opening_declared: true, fill_count: 0, cost_value: 0 },
    z_line: { ipo_win: 0, ipo_sell: 0, cb_win: 0, cb_sell: 0, cash_yield: 0, records: 0, realized_pnl: 0 },
    recent_ledger_rows: [],
    monthly_drift: [],
    generated_at: '2026-08-24T11:00:00+08:00',
    ...over,
  }
}

async function mountView() {
  const wrapper = mount(AccountLines, { global: { stubs: globalStubs } })
  await flushPromises()
  return wrapper
}

describe('AccountLines view', () => {
  beforeEach(() => {
    getMock.mockReset()
  })

  it('renders declared cash and the empty-position note', async () => {
    getMock.mockResolvedValue(payload())
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('150,000.00 元')
    expect(wrapper.text()).toContain('已申报')
    expect(wrapper.text()).toContain('(无持仓)')
    expect(wrapper.text()).toContain('账本尚无成交')
  })

  it('flags an undeclared opening and lists positions + ledger rows', async () => {
    getMock.mockResolvedValue(
      payload({
        r_line: {
          positions: [{ code: '002271', volume: 4900, avg_cost: 12.3123 }],
          cash: -61509.85, opening_declared: false, fill_count: 1, cost_value: 60330.27,
        },
        recent_ledger_rows: [
          { kind: 'adjust', recorded_at: '2026-08-24T18:00:00+08:00', code: '002271', volume_delta: -100 },
          { kind: 'fill', recorded_at: '2026-08-24T18:00:00+08:00', code: '002271', side: 'BUY',
            volume: 5000, price: 12.3, net: 61509.85, commission: 9.23, stamp_tax: 0, transfer_fee: 0.62,
            executed_at: '2026-08-24T10:12:00+08:00' },
        ],
        monthly_drift: [{ month: '202608', comparable_fills: 1, uncovered_fills: 0, drift_yuan: 1500, drift_pct: 2.5 }],
      }),
    )
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('未申报')
    expect(wrapper.find('.position-table').attributes('data-rows')).toBe('1')
    expect(wrapper.find('.ledger-table').attributes('data-rows')).toBe('2')
    expect(wrapper.find('.drift-table').attributes('data-rows')).toBe('1')
  })

  it('shows the API error in a banner', async () => {
    getMock.mockRejectedValue(new Error('unknown kind'))
    const wrapper = await mountView()
    expect(wrapper.find('.banner-error').text()).toContain('unknown kind')
    expect(wrapper.find('.line-grid').exists()).toBe(false)
  })

  it('re-fetches on refresh click', async () => {
    getMock.mockResolvedValue(payload())
    const wrapper = await mountView()
    await wrapper.find('.el-button').trigger('click')
    await flushPromises()
    expect(getMock).toHaveBeenCalledTimes(2)
  })
})
