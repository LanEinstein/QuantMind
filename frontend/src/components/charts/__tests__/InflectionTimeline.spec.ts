import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import InflectionTimeline from '@/components/charts/InflectionTimeline.vue'
import type { EnrichedInflectionViewModel } from '@/types/simulation'

const globalStubs = {
  // el-tag from Element Plus
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  // MiniSentimentDonut uses ECharts/Canvas — stub to avoid jsdom canvas errors
  MiniSentimentDonut: { template: '<div class="mini-donut-stub" />' },
}

function mountTimeline(inflectionPoints: readonly EnrichedInflectionViewModel[]) {
  return mount(InflectionTimeline, {
    props: { inflectionPoints },
    global: { stubs: globalStubs },
  })
}

function makePoint(overrides: Partial<EnrichedInflectionViewModel> = {}): EnrichedInflectionViewModel {
  return {
    day: 1,
    event: 'Test event',
    inflection_type: 'sentiment_reversal',
    before_sentiment: { bullish: 0.4, bearish: 0.35, neutral: 0.25 },
    after_sentiment: { bullish: 0.6, bearish: 0.25, neutral: 0.15 },
    confidence: 0.75,
    ...overrides,
  }
}

describe('InflectionTimeline', () => {
  describe('dot class reflects inflection_type', () => {
    const cases: Array<[EnrichedInflectionViewModel['inflection_type'], string]> = [
      ['sentiment_reversal', 'type-reversal'],
      ['narrative_convergence', 'type-convergence'],
      ['cascade_trigger', 'type-cascade'],
      ['exhaustion', 'type-exhaustion'],
      ['', 'type-unknown'],
    ]
    cases.forEach(([type, cls]) => {
      it(`${type || '(empty)'} → ${cls}`, () => {
        const wrapper = mountTimeline([makePoint({ inflection_type: type })])
        expect(wrapper.find('.marker-dot').classes()).toContain(cls)
      })
    })
  })

  describe('dot size scales with confidence', () => {
    it('confidence 0.0 → 8px dot', () => {
      const wrapper = mountTimeline([makePoint({ confidence: 0.0 })])
      const style = wrapper.find('.marker-dot').attributes('style') ?? ''
      expect(style).toContain('8px')
    })

    it('confidence 1.0 → 16px dot', () => {
      const wrapper = mountTimeline([makePoint({ confidence: 1.0 })])
      const style = wrapper.find('.marker-dot').attributes('style') ?? ''
      expect(style).toContain('16px')
    })

    it('confidence 0.5 → 12px dot', () => {
      const wrapper = mountTimeline([makePoint({ confidence: 0.5 })])
      const style = wrapper.find('.marker-dot').attributes('style') ?? ''
      expect(style).toContain('12px')
    })
  })

  describe('click emits seek event', () => {
    it('click on timeline item emits seek with item.day', async () => {
      const wrapper = mountTimeline([
        makePoint({ day: 7, event: '事件A' }),
        makePoint({ day: 12, event: '事件B' }),
      ])
      await wrapper.findAll('.timeline-item')[1].trigger('click')
      expect(wrapper.emitted('seek')?.[0]).toEqual([12])
    })

    it('click on first item emits its day', async () => {
      const wrapper = mountTimeline([makePoint({ day: 3 })])
      await wrapper.find('.timeline-item').trigger('click')
      expect(wrapper.emitted('seek')?.[0]).toEqual([3])
    })
  })

  describe('empty state', () => {
    it('shows empty message when no inflection points given', () => {
      const wrapper = mountTimeline([])
      expect(wrapper.find('.timeline-empty').exists()).toBe(true)
      expect(wrapper.text()).toContain('暂无拐点数据')
    })

    it('hides empty message when at least one point exists', () => {
      const wrapper = mountTimeline([makePoint()])
      expect(wrapper.find('.timeline-empty').exists()).toBe(false)
    })
  })

  describe('sentiment snapshot display (non-hover state)', () => {
    it('shows before and after bullish percentages', () => {
      const wrapper = mountTimeline([
        makePoint({
          before_sentiment: { bullish: 0.3, bearish: 0.5, neutral: 0.2 },
          after_sentiment: { bullish: 0.7, bearish: 0.2, neutral: 0.1 },
        }),
      ])
      const text = wrapper.text()
      expect(text).toContain('30%')
      expect(text).toContain('70%')
    })

    it('renders event text', () => {
      const wrapper = mountTimeline([makePoint({ event: '流动性危机触发级联' })])
      expect(wrapper.text()).toContain('流动性危机触发级联')
    })
  })
})
