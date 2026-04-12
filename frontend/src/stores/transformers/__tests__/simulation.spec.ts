import { describe, it, expect } from 'vitest'
import {
  splitScenariosByDirection,
  fallbackBeforeAfterFromSentiment,
  enrichInflection,
} from '@/stores/transformers/simulation'
import type { SentimentSnapshot, InflectionPoint } from '@/types/simulation'

describe('splitScenariosByDirection', () => {
  it('separates by direction field', () => {
    const scenarios = [
      { scenario: 'A', probability: 0.1, impact: '+5%', direction: 'upside' as const },
      { scenario: 'B', probability: 0.2, impact: '-3%', direction: 'downside' as const },
      { scenario: 'C', probability: 0.15, impact: '+2%', direction: 'upside' as const },
    ]
    const { upside, downside } = splitScenariosByDirection(scenarios)
    expect(upside).toHaveLength(2)
    expect(downside).toHaveLength(1)
    expect(upside[0].scenario).toBe('A')
    expect(downside[0].scenario).toBe('B')
  })

  it('missing or empty direction routes to downside (conservative risk bias)', () => {
    const scenarios = [
      { scenario: 'Unknown', probability: 0.5, impact: '0%', direction: '' as const },
    ]
    const { upside, downside } = splitScenariosByDirection(scenarios)
    expect(upside).toHaveLength(0)
    expect(downside).toHaveLength(1)
    expect(downside[0].scenario).toBe('Unknown')
  })

  it('handles empty array', () => {
    const { upside, downside } = splitScenariosByDirection([])
    expect(upside).toHaveLength(0)
    expect(downside).toHaveLength(0)
  })
})

describe('fallbackBeforeAfterFromSentiment', () => {
  const sentiment: readonly SentimentSnapshot[] = [
    { round: 1, bullish: 0.4, bearish: 0.3, neutral: 0.3 },
    { round: 2, bullish: 0.5, bearish: 0.2, neutral: 0.3 },
    { round: 3, bullish: 0.35, bearish: 0.45, neutral: 0.2 },
  ]

  it('returns neighbouring snapshots', () => {
    const { before, after } = fallbackBeforeAfterFromSentiment(2, sentiment)
    expect(before['bullish']).toBe(0.4) // round 1
    expect(after['bullish']).toBe(0.5)  // round 2
  })

  it('returns empty object when snapshot not found', () => {
    const { before, after } = fallbackBeforeAfterFromSentiment(10, sentiment)
    expect(Object.keys(before)).toHaveLength(0)
    expect(Object.keys(after)).toHaveLength(0)
  })
})

describe('enrichInflection', () => {
  const sentiment: readonly SentimentSnapshot[] = [
    { round: 1, bullish: 0.4, bearish: 0.3, neutral: 0.3 },
    { round: 2, bullish: 0.6, bearish: 0.2, neutral: 0.2 },
  ]

  it('prefers backend data over fallback when both present', () => {
    const ip: InflectionPoint = {
      day: 2,
      event: '情绪逆转',
      inflection_type: 'sentiment_reversal',
      before_sentiment: { bullish: 0.1, bearish: 0.8, neutral: 0.1 },
      after_sentiment: { bullish: 0.7, bearish: 0.1, neutral: 0.2 },
      confidence: 0.9,
    }
    const vm = enrichInflection(ip, sentiment)
    expect(vm.before_sentiment['bullish']).toBe(0.1)  // backend value, not fallback 0.4
    expect(vm.after_sentiment['bullish']).toBe(0.7)
    expect(vm.confidence).toBe(0.9)
    expect(vm.inflection_type).toBe('sentiment_reversal')
  })

  it('falls back to sentiment data when before/after sentiment empty', () => {
    const ip: InflectionPoint = {
      day: 2,
      event: '数据落地',
      inflection_type: 'narrative_convergence',
    }
    const vm = enrichInflection(ip, sentiment)
    // Should use round 1 as before, round 2 as after
    expect(vm.before_sentiment['bullish']).toBe(0.4)
    expect(vm.after_sentiment['bullish']).toBe(0.6)
  })

  it('defaults confidence to 0.5 when missing', () => {
    const ip: InflectionPoint = { day: 1, event: '事件' }
    const vm = enrichInflection(ip, sentiment)
    expect(vm.confidence).toBe(0.5)
  })

  it('defaults inflection_type to empty string when missing', () => {
    const ip: InflectionPoint = { day: 1, event: '事件' }
    const vm = enrichInflection(ip, sentiment)
    expect(vm.inflection_type).toBe('')
  })
})
