/** API client for analysis history endpoints (GET-only per P1-5 §2).
 *
 * Backend contract after Phase A:
 *   GET /api/analysis/signals            → ApiEnvelope<TradingSignal[]>
 *   GET /api/analysis/signal-accuracy    → ApiEnvelope<...>
 *   GET /api/analysis/history            → ApiEnvelope<AnalysisSummary[]>
 *   GET /api/analysis/{record_id}        → ApiEnvelope<AnalysisDetail>
 *
 * The manual-trigger POSTs (/stock, /jobs) and the live SSE stream
 * (/stream/{job_id}) were destructively deleted; analysis is driven
 * exclusively by the Fast/Slow scheduler.
 */

import { apiGet } from './request'
import type { AnalysisSummary, AnalysisDetail } from '@/types/agent'

export interface HistoryQuery {
  readonly stock_code?: string
  readonly trade_date?: string
  readonly limit?: number
}

export const analysisApi = {
  async getDetail(id: string): Promise<AnalysisDetail> {
    return apiGet<AnalysisDetail>(
      `/api/analysis/${encodeURIComponent(id)}`,
    )
  },

  async getHistory(params?: HistoryQuery): Promise<AnalysisSummary[]> {
    const query: Record<string, unknown> = {}
    if (params?.stock_code) query.stock_code = params.stock_code
    if (params?.trade_date) query.trade_date = params.trade_date
    if (params?.limit !== undefined) query.limit = params.limit
    return apiGet<AnalysisSummary[]>('/api/analysis/history', query)
  },
}
