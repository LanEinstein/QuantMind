import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ReadinessKpiPanel from '@/components/performance/ReadinessKpiPanel.vue'
import { performanceApi } from '@/api/performance'
import { acceptanceApi } from '@/api/acceptance'
import type { EquityKpisPayload } from '@/types/performance'
import type { AcceptanceLatestPayload } from '@/types/acceptance'

vi.mock('@/api/performance', () => ({
  performanceApi: { getEquityKpis: vi.fn() },
}))
vi.mock('@/api/acceptance', () => ({
  acceptanceApi: { getLatest: vi.fn() },
}))

const kpisMock = vi.mocked(performanceApi.getEquityKpis)
const acceptanceMock = vi.mocked(acceptanceApi.getLatest)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  ElTooltip: { template: '<span><slot /></span>' },
}

function kpisPayload(over: Partial<EquityKpisPayload> = {}): EquityKpisPayload {
  return {
    kpis: {
      total_return: 0.12,
      annualized_return: 0.31,
      annualized_reliable: true,
      max_drawdown: -0.05,
      sharpe_ratio: 1.4,
      hs300_excess: 0.06,
      sample_trading_days: 60,
      policy_segment_count: 2,
      data_quality: { FRESH: 60 },
      latest_total_equity: 1_120_000,
    },
    equity_series: [],
    policy_segments: [],
    active_policy_hash: 'abc',
    repository_status: 'ok',
    ...over,
  }
}

function acceptancePayload(
  over: Partial<AcceptanceLatestPayload> = {},
): AcceptanceLatestPayload {
  return {
    report: {
      report_id: 'r1',
      computed_at: '2026-06-12T08:00:30+00:00',
      trade_date: '2026-06-12',
      window_start: '2026-04-28',
      window_end: '2026-06-12',
      trading_days_in_window: 45,
      outcome: 'PASS',
      metrics: [
        {
          name: 'instruction_completion_rate',
          value: 0.97,
          threshold: 0.95,
          direction: 'at_least',
          passed: true,
        },
        {
          name: 'max_drawdown_pct',
          value: 0.05,
          threshold: 0.08,
          direction: 'at_most',
          passed: true,
        },
      ],
      notes: '',
    },
    can_switch_to_feishu_on: true,
    service_status: 'ok',
    ...over,
  }
}

async function mountPanel() {
  const wrapper = mount(ReadinessKpiPanel, { global: { stubs: globalStubs } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  kpisMock.mockReset()
  acceptanceMock.mockReset()
})

describe('ReadinessKpiPanel (AD-001)', () => {
  it('renders EquityPoint KPI tiles + gate chips', async () => {
    kpisMock.mockResolvedValue(kpisPayload())
    acceptanceMock.mockResolvedValue(acceptancePayload())
    const wrapper = await mountPanel()
    expect(wrapper.findAll('.kpi-tile').length).toBeGreaterThanOrEqual(6)
    expect(wrapper.text()).toContain('+12.00%') // total return
    expect(wrapper.text()).toContain('可切换实盘')
    expect(wrapper.findAll('.gate-chip')).toHaveLength(2)
    expect(wrapper.findAll('.gate-pass')).toHaveLength(2)
  })

  it('flags short-window annualized as unreliable', async () => {
    kpisMock.mockResolvedValue(
      kpisPayload({
        kpis: {
          ...kpisPayload().kpis,
          sample_trading_days: 10,
          annualized_reliable: false,
        },
      }),
    )
    acceptanceMock.mockResolvedValue(acceptancePayload({ report: null }))
    const wrapper = await mountPanel()
    expect(wrapper.find('.kpi-value.faded').exists()).toBe(true)
  })

  it('surfaces a load error', async () => {
    kpisMock.mockRejectedValue(new Error('boom'))
    acceptanceMock.mockResolvedValue(acceptancePayload())
    const wrapper = await mountPanel()
    expect(wrapper.find('.error-text').text()).toContain('boom')
  })
})
