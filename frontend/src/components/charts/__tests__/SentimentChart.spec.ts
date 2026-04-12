import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import SentimentChart from '@/components/charts/SentimentChart.vue'
import { PLAYBACK_KEY } from '@/composables/usePlayback'
import type { SentimentSnapshot, InflectionPoint } from '@/types/simulation'

function readOption(wrapper: VueWrapper<unknown>): Record<string, unknown> {
  const vchart = wrapper.findComponent({ name: 'VChart' })
  return vchart.props('option') as Record<string, unknown>
}

function makeSentimentData(count: number): readonly SentimentSnapshot[] {
  return Array.from({ length: count }, (_, i) => ({
    round: i + 1,
    bullish: 0.4 + i * 0.02,
    bearish: 0.35,
    neutral: 0.25 - i * 0.02,
    intensity: 0.5 + i * 0.05,
  }))
}

function makeInflections(): readonly InflectionPoint[] {
  return [
    { day: 2, event: '政策出台', inflection_type: 'sentiment_reversal', confidence: 0.8 },
    { day: 5, event: '数据落地', inflection_type: 'narrative_convergence', confidence: 0.6 },
    { day: 8, event: '外部冲击', inflection_type: 'cascade_trigger', confidence: 0.9 },
  ]
}

function makePlayback(currentRound: number) {
  return {
    currentRound: ref(currentRound),
    isPlaying: ref(false),
    play: vi.fn(),
    pause: vi.fn(),
    toggle: vi.fn(),
    step: vi.fn(),
    seek: vi.fn(),
    reset: vi.fn(),
  }
}

function mountChart(
  sentimentData: readonly SentimentSnapshot[],
  inflectionPoints: readonly InflectionPoint[],
  currentRound: number,
) {
  return mount(SentimentChart, {
    props: { sentimentData, inflectionPoints },
    global: {
      provide: { [PLAYBACK_KEY as symbol]: makePlayback(currentRound) },
    },
  })
}

describe('SentimentChart', () => {
  describe('currentRound injection drives data slicing', () => {
    it('shows only rounds 1..currentRound', async () => {
      const wrapper = mountChart(makeSentimentData(10), [], 4)
      await Promise.resolve()

      const xAxis = (readOption(wrapper) as { xAxis: { data: string[] } }).xAxis
      expect(xAxis.data).toHaveLength(4)
      expect(xAxis.data[0]).toBe('R1')
      expect(xAxis.data[3]).toBe('R4')
    })

    it('shows all rounds when currentRound equals total', async () => {
      const wrapper = mountChart(makeSentimentData(5), [], 5)
      await Promise.resolve()

      const xAxis = (readOption(wrapper) as { xAxis: { data: string[] } }).xAxis
      expect(xAxis.data).toHaveLength(5)
    })

    it('falls back to showing all data when no playback provided', async () => {
      const data = makeSentimentData(7)
      const wrapper = mount(SentimentChart, {
        props: { sentimentData: data, inflectionPoints: [] },
      })
      await Promise.resolve()

      const xAxis = (readOption(wrapper) as { xAxis: { data: string[] } }).xAxis
      expect(xAxis.data).toHaveLength(7)
    })
  })

  describe('intensity field drives area opacity', () => {
    it('intensity 0.0 → opacity ≈ 0.3', async () => {
      const data: readonly SentimentSnapshot[] = [
        { round: 1, bullish: 0.4, bearish: 0.35, neutral: 0.25, intensity: 0.0 },
      ]
      const wrapper = mountChart(data, [], 1)
      await Promise.resolve()

      const series = (readOption(wrapper) as { series: Array<{ areaStyle?: { opacity: number } }> }).series
      expect(series[0].areaStyle?.opacity).toBeCloseTo(0.3, 1)
    })

    it('intensity 1.0 → opacity ≈ 0.9', async () => {
      const data: readonly SentimentSnapshot[] = [
        { round: 1, bullish: 0.6, bearish: 0.2, neutral: 0.2, intensity: 1.0 },
      ]
      const wrapper = mountChart(data, [], 1)
      await Promise.resolve()

      const series = (readOption(wrapper) as { series: Array<{ areaStyle?: { opacity: number } }> }).series
      expect(series[0].areaStyle?.opacity).toBeCloseTo(0.9, 1)
    })
  })

  describe('now markLine', () => {
    it('positions the now line at currentRound on the x-axis', async () => {
      const wrapper = mountChart(makeSentimentData(5), [], 3)
      await Promise.resolve()

      type Series = { markLine?: { data?: Array<{ xAxis?: string }> } }
      const series = (readOption(wrapper) as { series: Series[] }).series
      const allMarkData = series[0]?.markLine?.data ?? []
      const nowLine = allMarkData.find((d) => d.xAxis === 'R3')
      expect(nowLine).toBeDefined()
    })
  })

  describe('inflection markLines are filtered by currentRound', () => {
    it('only renders inflection markLines whose day <= currentRound', async () => {
      const wrapper = mountChart(makeSentimentData(10), makeInflections(), 5)
      await Promise.resolve()

      type MarkEntry = { xAxis?: string; lineStyle?: { type?: string } }
      type Series = { markLine?: { data?: MarkEntry[] } }
      const series = (readOption(wrapper) as { series: Series[] }).series
      const markData = series[0]?.markLine?.data ?? []

      const inflectionEntries = markData.filter((d) => d.lineStyle?.type === 'dashed')
      const inflectionAxes = inflectionEntries.map((d) => d.xAxis)
      expect(inflectionAxes).toEqual(expect.arrayContaining(['R2', 'R5']))
      expect(inflectionAxes).not.toContain('R8')
      expect(inflectionEntries).toHaveLength(2)
    })
  })
})
