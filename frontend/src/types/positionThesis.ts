/**
 * Z-003 — position-thesis tracking types.
 *
 * Mirrors ``GET /api/position-theses`` (P1-5-amendment-2026-06-01 §1.2
 * direction②). Display-only: pillars are LLM advisory text, invalidation
 * conditions are deterministic quant thresholds (LLM never influences them).
 */

export interface ThesisInvalidationCondition {
  readonly template: string
  readonly metric_name: string
  readonly comparator: string
  readonly threshold: number
  readonly anchor: number
}

export interface PositionThesisView {
  readonly stock_code: string
  readonly stock_name: string
  readonly instruction_id: string
  readonly trade_date: string
  readonly created_at: string
  readonly entry_price: number
  readonly entry_score: number
  readonly time_stop_trade_days: number
  readonly catalyst_window_end: string | null
  readonly pillars: readonly string[]
  readonly invalidation_conditions: readonly ThesisInvalidationCondition[]
  readonly evidence_ids: readonly string[]
  /** AD-004 — deterministic buy-time style label (AC-001), display-only.
   * `null` on legacy theses / the pure-quant path. */
  readonly style?: string | null
}

export interface PositionThesesPayload {
  readonly available: boolean
  readonly note: string
  readonly thesis_count: number
  readonly theses: readonly PositionThesisView[]
  readonly advisory: { readonly note: string }
}

/** Human-readable labels for the 3 whitelist invalidation templates. */
export const INVALIDATION_TEMPLATE_LABELS: Readonly<Record<string, string>> = {
  anchor_drawdown: '锚定回撤',
  time_stop: '时间止损',
  score_decay: '分数衰减',
}
