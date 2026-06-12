/**
 * Z-004 — ≤5-slot portfolio + rotation types.
 *
 * Mirrors ``GET /api/slot-rotation`` (P1-5-amendment-2026-06-01 §1.2
 * direction③). Display-only: the deterministic rotation ledger; quant stays
 * the qualification authority and the ≤5 cap is RiskEngine check#6.
 */

export interface RotationIntentView {
  readonly intent_id: string
  readonly created_trade_date: string
  readonly expires_at_trade_date: string
  readonly sell_instruction_id: string
  readonly incumbent_code: string
  readonly challenger_code: string
  readonly incumbent_score: number
  readonly challenger_score: number
  readonly incumbent_percentile: number
  readonly challenger_percentile: number
}

export interface RotationEventView {
  readonly event_type: string
  readonly trade_date: string
  readonly intent_id: string | null
  readonly incumbent_code: string | null
  readonly challenger_code: string | null
  readonly outcome_kind: string | null
  readonly buy_code: string | null
  readonly blocks_further_rotation: boolean
  readonly note: string
}

export interface SlotRotationPayload {
  readonly available: boolean
  readonly note: string
  readonly max_total_positions: number | null
  readonly underinvested_block_active: boolean
  readonly open_intent_count: number
  readonly open_intents: readonly RotationIntentView[]
  readonly recent_events: readonly RotationEventView[]
}

export const ROTATION_EVENT_LABELS: Readonly<Record<string, string>> = {
  proposed: '提议(卖出已发)',
  resolved: '完成/作废',
  expired: '到期',
  underinvested_cleared: '欠配解除',
}
