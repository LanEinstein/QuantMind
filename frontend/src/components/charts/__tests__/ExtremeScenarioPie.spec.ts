import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ExtremeScenarioPie from '@/components/charts/ExtremeScenarioPie.vue'
import type { ExtremeScenario } from '@/types/simulation'

function readOption(wrapper: VueWrapper<unknown>): Record<string, unknown> {
  const vchart = wrapper.findComponent({ name: 'VChart' })
  return vchart.props('option') as Record<string, unknown>
}

type SeriesEntry = {
  name: string
  startAngle: number
  endAngle: number
  center: string[]
  radius: string[]
  data: Array<{ name: string; value: number; _scenario?: ExtremeScenario }>
}

function mountPie(
  upsideScenarios: readonly ExtremeScenario[],
  downsideScenarios: readonly ExtremeScenario[],
  allScenarios: readonly ExtremeScenario[],
) {
  return mount(ExtremeScenarioPie, {
    props: { upsideScenarios, downsideScenarios, allScenarios },
  })
}

const makeScenario = (overrides: Partial<ExtremeScenario>): ExtremeScenario => ({
  scenario: 'default',
  probability: 0.1,
  impact: 'moderate',
  direction: '',
  trigger_conditions: '',
  early_warning_signals: '',
  ...overrides,
})

describe('ExtremeScenarioPie', () => {
  describe('hemisphere series configuration', () => {
    it('upside series uses startAngle:90, endAngle:-90 (right hemisphere)', async () => {
      const up = [makeScenario({ scenario: 'Rally', probability: 0.2, direction: 'upside' })]
      const wrapper = mountPie(up, [], up)
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const upSeries = series.find((s) => s.name === 'upside')
      expect(upSeries?.startAngle).toBe(90)
      expect(upSeries?.endAngle).toBe(-90)
    })

    it('downside series uses startAngle:90, endAngle:270 (left hemisphere)', async () => {
      const down = [makeScenario({ scenario: 'Crash', probability: 0.15, direction: 'downside' })]
      const wrapper = mountPie([], down, down)
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const downSeries = series.find((s) => s.name === 'downside')
      expect(downSeries?.startAngle).toBe(90)
      expect(downSeries?.endAngle).toBe(270)
    })

    it('both series share the same center point', async () => {
      const up = [makeScenario({ direction: 'upside', probability: 0.1 })]
      const down = [makeScenario({ direction: 'downside', probability: 0.1 })]
      const wrapper = mountPie(up, down, [...up, ...down])
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      expect(series[0].center).toEqual(['50%', '50%'])
      expect(series[1].center).toEqual(['50%', '50%'])
    })
  })

  describe('baseline probability slice', () => {
    it('includes baseline when total scenario probabilities < 1', async () => {
      const up = [makeScenario({ direction: 'upside', probability: 0.2 })]
      const down = [makeScenario({ direction: 'downside', probability: 0.1 })]
      const wrapper = mountPie(up, down, [...up, ...down])
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const hasBaseline = series.some((s) => s.data.some((d) => d.name.startsWith('基准')))
      expect(hasBaseline).toBe(true)
    })

    it('omits baseline when total probability equals 1', async () => {
      const up = [makeScenario({ direction: 'upside', probability: 0.5 })]
      const down = [makeScenario({ direction: 'downside', probability: 0.5 })]
      const wrapper = mountPie(up, down, [...up, ...down])
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const hasBaseline = series.some((s) => s.data.some((d) => d.name.startsWith('基准')))
      expect(hasBaseline).toBe(false)
    })
  })

  describe('data accuracy', () => {
    it('upside data contains scenarios with probability converted to percent', async () => {
      const up = [
        makeScenario({ scenario: 'MegaRally', probability: 0.15, direction: 'upside' }),
        makeScenario({ scenario: 'MildRally', probability: 0.1, direction: 'upside' }),
      ]
      const wrapper = mountPie(up, [], up)
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const upsideData = series[0].data.filter((d) => !d.name.startsWith('基准'))
      expect(upsideData.find((d) => d.name === 'MegaRally')?.value).toBe(15)
      expect(upsideData.find((d) => d.name === 'MildRally')?.value).toBe(10)
    })

    it('scenario data includes _scenario object for click handler', async () => {
      const scenario = makeScenario({ scenario: 'EarlyWarningTest', direction: 'upside', probability: 0.2 })
      const wrapper = mountPie([scenario], [], [scenario])
      await Promise.resolve()

      const series = readOption(wrapper)?.series as SeriesEntry[]
      const entry = series[0].data.find((d) => d.name === 'EarlyWarningTest')
      expect(entry?._scenario?.scenario).toBe('EarlyWarningTest')
    })
  })

  describe('click event handling', () => {
    it('does not emit open-scenario when slice has no _scenario', async () => {
      const wrapper = mountPie([], [], [])
      await wrapper.findComponent({ name: 'VChart' }).trigger('click', { data: { name: 'baseline', value: 50 } })
      expect(wrapper.emitted('open-scenario')).toBeUndefined()
    })

    it('handles missing direction gracefully without throwing', () => {
      const scenario = makeScenario({ direction: undefined as unknown as string })
      expect(() => mountPie([], [], [scenario])).not.toThrow()
    })
  })
})
