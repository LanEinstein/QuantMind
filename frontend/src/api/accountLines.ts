/**
 * Post-MI-1 account-lines panel — client for the standalone read-only API
 * (`scripts/account_api.py`, 127.0.0.1:8001 via the Vite `/api` proxy).
 * Shape mirrors `scripts/account_view.py --json` plus recent ledger rows
 * and the monthly execution-drift disclosure.
 */

import { apiGet } from './request'

export interface MirrorPosition {
  code: string
  volume: number
  /** Fee-inclusive average cost (NOT a market price — broker app is truth). */
  avg_cost: number
}

export interface RLine {
  positions: MirrorPosition[]
  cash: number
  opening_declared: boolean
  fill_count: number
  cost_value: number
}

export interface ZLine {
  ipo_win: number
  ipo_sell: number
  cb_win: number
  cb_sell: number
  cash_yield: number
  records: number
  realized_pnl: number
}

/** One mirror-ledger row; fields beyond `kind`/`recorded_at` are kind-specific. */
export interface LedgerRow {
  kind: 'fill' | 'cash' | 'adjust'
  recorded_at: string
  effective_at?: string
  note?: string | null
  // fill
  code?: string
  side?: 'BUY' | 'SELL'
  volume?: number
  price?: number
  executed_at?: string
  reason?: string
  commission?: number
  stamp_tax?: number
  transfer_fee?: number
  gross?: number
  net?: number
  // cash
  amount?: number
  // adjust
  volume_delta?: number
}

export interface MonthlyDrift {
  month: string
  comparable_fills: number
  uncovered_fills: number
  drift_yuan: number
  drift_pct: number
}

export interface AccountLinesPayload {
  r_line: RLine
  z_line: ZLine
  recent_ledger_rows: LedgerRow[]
  monthly_drift: MonthlyDrift[]
  generated_at: string
}

export const accountLinesApi = {
  get(): Promise<AccountLinesPayload> {
    return apiGet<AccountLinesPayload>('/api/portfolio/lines')
  },
}
