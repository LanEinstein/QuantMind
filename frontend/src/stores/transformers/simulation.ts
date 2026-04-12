/** Pure transformer functions for simulation data.
 *
 * All functions are stateless and side-effect-free, making them
 * independently testable with Vitest without Vue component setup.
 */

import type {
  ExtremeScenario,
  SentimentSnapshot,
  InflectionPoint,
  InflectionType,
  EnrichedInflectionViewModel,
} from '@/types/simulation'

export interface SplitScenarios {
  readonly upside: readonly ExtremeScenario[]
  readonly downside: readonly ExtremeScenario[]
}

/** Partition extreme scenarios by direction field.
 *
 * Invariant: every input scenario is placed into exactly one bucket.
 * Scenarios with missing or unrecognised direction fall into `downside`
 * (conservative risk-viz bias — unknown risk should be shown, not hidden).
 */
export function splitScenariosByDirection(
  scenarios: readonly ExtremeScenario[],
): SplitScenarios {
  const upside: ExtremeScenario[] = []
  const downside: ExtremeScenario[] = []

  for (const s of scenarios) {
    if (s.direction === 'upside') {
      upside.push(s)
    } else {
      downside.push(s)
    }
  }

  return { upside, downside }
}

/** Derive before/after sentiment from neighbouring snapshots when backend data is absent. */
export function fallbackBeforeAfterFromSentiment(
  day: number,
  sentiment: readonly SentimentSnapshot[],
): {
  readonly before: Readonly<Record<string, number>>
  readonly after: Readonly<Record<string, number>>
} {
  // Snapshots are indexed by round (1-based), day maps directly
  const before = sentiment.find((s) => s.round === day - 1)
  const after = sentiment.find((s) => s.round === day)

  const toRecord = (s: SentimentSnapshot | undefined): Readonly<Record<string, number>> => {
    if (!s) return {}
    return { bullish: s.bullish, bearish: s.bearish, neutral: s.neutral }
  }

  return { before: toRecord(before), after: toRecord(after) }
}

/** Enrich an InflectionPoint with resolved before/after sentiment and safe defaults. */
export function enrichInflection(
  ip: InflectionPoint,
  sentiment: readonly SentimentSnapshot[],
): EnrichedInflectionViewModel {
  const hasBefore =
    ip.before_sentiment != null && Object.keys(ip.before_sentiment).length > 0
  const hasAfter =
    ip.after_sentiment != null && Object.keys(ip.after_sentiment).length > 0

  if (hasBefore && hasAfter) {
    return {
      day: ip.day,
      event: ip.event,
      inflection_type: (ip.inflection_type ?? '') as InflectionType,
      before_sentiment: ip.before_sentiment!,
      after_sentiment: ip.after_sentiment!,
      confidence: ip.confidence ?? 0.5,
    }
  }

  // Fall back to neighbouring sentiment snapshots
  const { before, after } = fallbackBeforeAfterFromSentiment(ip.day, sentiment)
  return {
    day: ip.day,
    event: ip.event,
    inflection_type: (ip.inflection_type ?? '') as InflectionType,
    before_sentiment: hasBefore ? ip.before_sentiment! : before,
    after_sentiment: hasAfter ? ip.after_sentiment! : after,
    confidence: ip.confidence ?? 0.5,
  }
}
