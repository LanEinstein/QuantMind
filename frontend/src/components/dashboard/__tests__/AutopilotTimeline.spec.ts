import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AutopilotTimeline from '@/components/dashboard/AutopilotTimeline.vue'
import { instructionPlansApi } from '@/api/instructionPlans'
import { slotRotationApi } from '@/api/slotRotation'
import { acceptanceApi } from '@/api/acceptance'
import { dualLineStatusApi } from '@/api/dualLineStatus'

vi.mock('@/api/instructionPlans', () => ({
  instructionPlansApi: { list: vi.fn(), get: vi.fn() },
}))
vi.mock('@/api/slotRotation', () => ({ slotRotationApi: { get: vi.fn() } }))
vi.mock('@/api/acceptance', () => ({ acceptanceApi: { getLatest: vi.fn() } }))
vi.mock('@/api/dualLineStatus', () => ({ dualLineStatusApi: { get: vi.fn() } }))

const listMock = vi.mocked(instructionPlansApi.list)
const rotMock = vi.mocked(slotRotationApi.get)
const accMock = vi.mocked(acceptanceApi.getLatest)
const dlMock = vi.mocked(dualLineStatusApi.get)

const globalStubs = {
  ElCard: { template: '<div class="el-card"><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
  ElTimeline: { template: '<div class="el-timeline"><slot /></div>' },
  ElTimelineItem: { template: '<div class="el-timeline-item"><slot /></div>' },
}

function today(): string {
  const d = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

async function mountPanel() {
  const wrapper = mount(AutopilotTimeline, { global: { stubs: globalStubs } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  listMock.mockReset()
  rotMock.mockReset()
  accMock.mockReset()
  dlMock.mockReset()
  rotMock.mockResolvedValue(null as never)
  accMock.mockResolvedValue(null as never)
  dlMock.mockResolvedValue(null as never)
})

describe('AutopilotTimeline (AD-002)', () => {
  it('renders the 7 pipeline stages', async () => {
    listMock.mockResolvedValue({
      plans: [],
      total: 0,
      repository_status: 'unavailable',
    })
    const wrapper = await mountPanel()
    expect(wrapper.findAll('.el-timeline-item')).toHaveLength(7)
    expect(wrapper.text()).toContain('筛选')
    expect(wrapper.text()).toContain('盘后复盘')
  })

  it('marks stages active from today plans', async () => {
    const td = today()
    listMock.mockResolvedValue({
      plans: [
        {
          instruction_id: 'QM-20260612-093500-600519-BUY-001',
          trade_date: td,
          stock_code: '600519',
          stock_name: '贵州茅台',
          side: 'BUY',
          status: 'FILLED',
          volume: 100,
          limit_price: 1700,
          valid_until: `${td}T09:40:00+00:00`,
          created_at: `${td}T09:35:00+00:00`,
          rejection_reason: null,
        },
      ],
      total: 1,
      repository_status: 'ok',
    })
    const wrapper = await mountPanel()
    expect(wrapper.findAll('.stage-summary.active').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.text()).toContain('1 条已成交入账')
  })

  it('surfaces a load error', async () => {
    listMock.mockRejectedValue(new Error('boom'))
    const wrapper = await mountPanel()
    expect(wrapper.find('.error-text').text()).toContain('boom')
  })
})
