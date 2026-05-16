/** G-008 — Data quality probe (per-stock 7+3 matrix). */

import { apiGet } from './request'

export interface DataQualityStatePayload {
  stock_code: string
  evaluated_at: string | null
  // 7 breach bools (P1-2.B §1.5.1 locked schema).
  quote_unavailable: boolean | null
  quote_staleness_breach: boolean | null
  quote_divergence_breach: boolean | null
  minimum_freshness_breach: boolean | null
  news_outage_breach: boolean | null
  mirofish_unavailable: boolean | null
  watchlist_snapshot_outage: boolean | null
  // 3 counters.
  primary_quote_age_seconds: number | null
  backup_quote_age_seconds: number | null
  news_sources_alive_count: number | null
  // 2 derived (composed server-side).
  is_acceptable_for_buy_sell: boolean | null
  degradation_reason: string | null
  blocking_breaches: string[] | null
}

export interface DataQualityPayload {
  status: 'ok' | 'unavailable'
  stock_code?: string
  reason?: string
  state?: DataQualityStatePayload
  timestamp?: string
}

export const dataQualityApi = {
  get(stockCode: string): Promise<DataQualityPayload> {
    return apiGet<DataQualityPayload>('/api/data-quality', {
      stock_code: stockCode,
    })
  },
}
