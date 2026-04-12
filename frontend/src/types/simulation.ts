/** TypeScript interfaces for MiroFish simulation visualization.
 *
 * Mirrors backend Pydantic models in backend/mirofish/schemas.py,
 * enriched with API-layer fields (id, event, created_at).
 * All new enriched fields are optional to tolerate legacy API responses.
 */

export interface SimulationConfig {
  readonly agent_count: number
  readonly rounds: number
  readonly model: string
}

export interface MomentumShift {
  readonly round_number: number
  readonly direction: 'bullish_to_bearish' | 'bearish_to_bullish'
  readonly magnitude: number
  readonly trigger_narrative?: string
}

export interface SentimentSnapshot {
  readonly round: number
  readonly bullish: number
  readonly bearish: number
  readonly neutral: number
  readonly dominant_narrative?: string
  readonly intensity?: number
}

export interface HiddenVariable {
  readonly variable: string
  readonly probability: number
  readonly reasoning: string
  readonly agent_consensus_ratio?: number
  readonly is_absent_from_original?: boolean
}

export type InflectionType =
  | 'sentiment_reversal'
  | 'narrative_convergence'
  | 'cascade_trigger'
  | 'exhaustion'
  | ''

export interface InflectionPoint {
  readonly day: number
  readonly event: string
  readonly inflection_type?: InflectionType
  readonly before_sentiment?: Readonly<Record<string, number>>
  readonly after_sentiment?: Readonly<Record<string, number>>
  readonly confidence?: number
}

export interface ExtremeScenario {
  readonly scenario: string
  readonly probability: number
  readonly impact: string
  readonly direction?: 'upside' | 'downside' | ''
  readonly trigger_conditions?: string
  readonly early_warning_signals?: string
}

export interface EventDescription {
  readonly title: string
  readonly content: string
  readonly importance_score: number
  readonly sectors: readonly string[]
  readonly stocks: readonly string[]
}

/** Complete simulation result — backend SimulationResult + API enrichment. */
export interface SimulationResult {
  readonly id: string
  readonly event: EventDescription
  readonly event_summary: string
  readonly simulation_config: SimulationConfig
  readonly sentiment_evolution: readonly SentimentSnapshot[]
  readonly hidden_variables: readonly HiddenVariable[]
  readonly key_inflection_points: readonly InflectionPoint[]
  readonly extreme_scenarios: readonly ExtremeScenario[]
  readonly momentum_shifts?: readonly MomentumShift[]
  readonly recommended_action: string
  readonly cost_rmb: number
  readonly duration_seconds: number
  readonly created_at: string
}

/** View model for InflectionTimeline — enriched with fallback-resolved before/after. */
export interface EnrichedInflectionViewModel {
  readonly day: number
  readonly event: string
  readonly inflection_type: InflectionType
  readonly before_sentiment: Readonly<Record<string, number>>
  readonly after_sentiment: Readonly<Record<string, number>>
  readonly confidence: number
}

/** Lightweight projection for history sidebar list. */
export interface SimulationHistoryItem {
  readonly id: string
  readonly event_title: string
  readonly importance_score: number
  readonly agent_count: number
  readonly rounds: number
  readonly recommended_action: string
  readonly cost_rmb: number
  readonly duration_seconds: number
  readonly created_at: string
}

/** Side-by-side comparison of two simulation results. */
export interface SimulationComparison {
  readonly a: SimulationResult
  readonly b: SimulationResult
}

export type SimulationStatus = 'idle' | 'loading' | 'loaded' | 'error'
