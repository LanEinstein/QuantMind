/** TypeScript interfaces for MiroFish simulation visualization.
 *
 * Mirrors backend Pydantic models in backend/mirofish/schemas.py,
 * enriched with API-layer fields (id, event, created_at).
 */

export interface SimulationConfig {
  readonly agent_count: number
  readonly rounds: number
  readonly model: string
}

export interface SentimentSnapshot {
  readonly round: number
  readonly bullish: number
  readonly bearish: number
  readonly neutral: number
}

export interface HiddenVariable {
  readonly variable: string
  readonly probability: number
  readonly reasoning: string
}

export interface InflectionPoint {
  readonly day: number
  readonly event: string
}

export interface ExtremeScenario {
  readonly scenario: string
  readonly probability: number
  readonly impact: string
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
  readonly recommended_action: string
  readonly cost_rmb: number
  readonly duration_seconds: number
  readonly created_at: string
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
