/**
 * AD-005 — manual-trade (user-discretionary) types.
 *
 * Mirrors `POST /api/manual-trades` (P1-5-amendment-2026-06-12). The third
 * (and only newly-added) write endpoint: the owner records a trade they did
 * on their own that the system did NOT instruct. The `UT-` id is disjoint
 * from `QM-` so it can never be mistaken for an instruction.
 */

export type ManualTradeSide = 'BUY' | 'SELL'

export type ManualTradeReason =
  | 'USER_TAKE_PROFIT'
  | 'USER_STOP_LOSS'
  | 'USER_ADD'
  | 'USER_OTHER'

export interface ManualTradeRequest {
  readonly external_trade_id: string
  readonly code: string
  readonly side: ManualTradeSide
  readonly volume: number
  readonly price: number
  readonly executed_at: string
  readonly reason: ManualTradeReason
  readonly note?: string
  readonly related_instruction_id?: string | null
}

export interface ManualTradeApplyResult {
  readonly cash_delta: number
  readonly positions_delta: readonly Record<string, unknown>[]
  readonly broker_event_sequence: number | null
  readonly reason: string
}

export interface ManualTradeResponse {
  readonly external_trade_id: string
  readonly feishu_sent: boolean
  readonly apply_result: ManualTradeApplyResult
}

export const MANUAL_TRADE_REASON_LABELS: Readonly<Record<ManualTradeReason, string>> = {
  USER_TAKE_PROFIT: '止盈',
  USER_STOP_LOSS: '止损',
  USER_ADD: '加仓',
  USER_OTHER: '其他',
}

export const MANUAL_TRADE_LOT_SIZE = 100
