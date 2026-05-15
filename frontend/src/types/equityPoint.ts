/** Front-end mirror of backend EquityPoint (G-004 read-only widget). */

export type EquityPointQuality = 'FRESH' | 'STALE' | 'DEGRADED' | 'EOD_FALLBACK'

export interface EquityPointPositionSnapshot {
  readonly code: string
  readonly volume: number
  readonly cost_price: number
  readonly last_price: number
  readonly market_value: number
  readonly unrealized_pnl: number
  readonly unrealized_pnl_pct: number
  readonly price_quality: EquityPointQuality
  readonly last_price_at: string | null
}

export interface EquityPointSnapshot {
  readonly snapshot_at: string
  readonly trade_date: string
  readonly cash: number
  readonly frozen_cash: number
  readonly market_value: number
  readonly total_equity: number
  readonly initial_capital: number
  readonly pnl: number
  readonly pnl_pct: number
  readonly quality: EquityPointQuality
  readonly last_broker_event_id: number | null
  readonly positions: readonly EquityPointPositionSnapshot[]
}

export interface EquityPointLatestPayload {
  readonly point: EquityPointSnapshot | null
  readonly repository_status: 'ok' | 'unavailable'
  readonly timestamp?: string
}
