/** API client for multi-agent stock analysis endpoints.
 *
 * Current backend (backend/api/analysis.py) exposes only:
 *   POST /api/analysis/stock  →  returns ApiEnvelope<TradingSignal>
 *
 * Future endpoints (GET /api/analysis/{id}, GET /api/analysis/history,
 * GET /api/analysis/stream/{id}) are planned but not yet implemented.
 * The client degrades gracefully: trigger() returns a synthetic id from
 * the TradingSignal, and getDetail()/getHistory() throw so the store
 * falls back to dev mocks.
 */

import { apiGet } from './request'
import instance from './request'
import type { AnalysisSummary, AnalysisDetail } from '@/types/agent'

export interface TriggerAnalysisParams {
  readonly stock_code: string
  readonly max_debate_rounds?: number
}

export interface TriggerResponse {
  readonly id: string
  readonly status: string
}

export const analysisApi = {
  /** Trigger a new analysis. Returns a synthetic id derived from stock_code + date. */
  async trigger(params: TriggerAnalysisParams): Promise<TriggerResponse> {
    const res = await instance.post('/api/analysis/stock', params)
    const envelope = res as unknown as { status: string; data: Record<string, unknown>; error: string | null }
    if (envelope.status === 'error') {
      throw new Error(envelope.error ?? 'Analysis trigger failed')
    }
    // Backend currently returns TradingSignal directly (no id field).
    // Synthesize an id from stock_code + trade_date for frontend routing.
    const data = envelope.data ?? {}
    const id = String(data.id ?? `${params.stock_code}-${data.trade_date ?? Date.now()}`)
    return { id, status: 'completed' }
  },

  /** Get analysis detail by id. (Backend endpoint not yet implemented.) */
  async getDetail(id: string): Promise<AnalysisDetail> {
    return apiGet<AnalysisDetail>(`/api/analysis/${encodeURIComponent(id)}`)
  },

  /** Get analysis history. (Backend endpoint not yet implemented.) */
  async getHistory(params?: {
    stock_code?: string
    date?: string
    limit?: number
  }): Promise<AnalysisSummary[]> {
    return apiGet<AnalysisSummary[]>('/api/analysis/history', params as Record<string, unknown>)
  },
}
