import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DualLineStatusPanel from '@/components/dashboard/DualLineStatusPanel.vue'
import { dualLineStatusApi } from '@/api/dualLineStatus'
import type { DualLineStatusPayload } from '@/types/dualLineStatus'

vi.mock('@/api/dualLineStatus', () => ({
  dualLineStatusApi: { get: vi.fn() },
}))

const getMock = vi.mocked(dualLineStatusApi.get)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
}

function payload(over: Partial<DualLineStatusPayload> = {}): DualLineStatusPayload {
  return {
    line1: { label: 'Line-1 选股', wired: true, max_debates_per_day: 8 },
    line2: { label: 'Line-2 监控', daily_wired: true, intraday_wired: true },
    rotation: { label: '≤5 槽轮动', wired: true, max_total_positions: 5 },
    scheduler_wired: true,
    note: 'polling, no new WS class',
    ...over,
  }
}

async function mountPanel() {
  const wrapper = mount(DualLineStatusPanel, { global: { stubs: globalStubs } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  getMock.mockReset()
})

describe('DualLineStatusPanel', () => {
  it('renders three line cards when all wired', async () => {
    getMock.mockResolvedValue(payload())
    const wrapper = await mountPanel()
    expect(wrapper.findAll('.line-card')).toHaveLength(3)
    expect(wrapper.findAll('.live-dot.on').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.text()).toContain('每日辩论上限')
    expect(wrapper.text()).toContain('持仓槽上限')
  })

  it('marks an unwired line as off', async () => {
    getMock.mockResolvedValue(
      payload({
        line1: { label: 'Line-1 选股', wired: false, max_debates_per_day: null },
      }),
    )
    const wrapper = await mountPanel()
    expect(wrapper.findAll('.live-dot.off').length).toBeGreaterThanOrEqual(1)
    expect(wrapper.text()).toContain('未接线')
  })

  it('surfaces a load error', async () => {
    getMock.mockRejectedValue(new Error('boom'))
    const wrapper = await mountPanel()
    expect(wrapper.find('.error-text').text()).toContain('boom')
  })
})
