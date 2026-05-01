/** API client for multi-agent stock analysis endpoints.
 *
 * Backend contract (post Phase-5 A1/A2):
 *   POST /api/analysis/stock              → ApiEnvelope<TradingSignal>
 *   GET  /api/analysis/signals            → ApiEnvelope<TradingSignal[]>
 *   GET  /api/analysis/signal-accuracy    → ApiEnvelope<...>
 *   GET  /api/analysis/history            → ApiEnvelope<AnalysisSummary[]>
 *   GET  /api/analysis/{record_id}        → ApiEnvelope<AnalysisDetail>
 *   POST /api/analysis/jobs               → ApiEnvelope<{ job_id, status }>
 *   GET  /api/analysis/stream/{job_id}    → text/event-stream
 */

import { apiGet, apiPost } from './request'
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

export interface CreateJobResponse {
  readonly job_id: string
  readonly status: string
}

export interface HistoryQuery {
  readonly stock_code?: string
  readonly trade_date?: string
  readonly limit?: number
}

export const analysisApi = {
  /**
   * Run a synchronous analysis. Returns a synthetic id derived from the
   * trading signal so the caller can stash a reference, even though the
   * real persistence id lives in the AnalysisRecord (see createJob flow).
   */
  async trigger(params: TriggerAnalysisParams): Promise<TriggerResponse> {
    const res = await instance.post('/api/analysis/stock', params)
    const envelope = res as unknown as {
      status: string
      data: Record<string, unknown>
      error: string | null
    }
    if (envelope.status === 'error') {
      throw new Error(envelope.error ?? 'Analysis trigger failed')
    }
    const data = envelope.data ?? {}
    const id = String(
      data.id ?? `${params.stock_code}-${data.trade_date ?? Date.now()}`,
    )
    return { id, status: 'completed' }
  },

  /** Fetch a full AnalysisRecord by id (ObjectId string or run_id UUID). */
  async getDetail(id: string): Promise<AnalysisDetail> {
    return apiGet<AnalysisDetail>(
      `/api/analysis/${encodeURIComponent(id)}`,
    )
  },

  /** List AgentDebate history rows from the analysis_records collection. */
  async getHistory(params?: HistoryQuery): Promise<AnalysisSummary[]> {
    const query: Record<string, unknown> = {}
    if (params?.stock_code) query.stock_code = params.stock_code
    if (params?.trade_date) query.trade_date = params.trade_date
    if (params?.limit !== undefined) query.limit = params.limit
    return apiGet<AnalysisSummary[]>('/api/analysis/history', query)
  },

  /** Create a live analysis job that publishes events over SSE. */
  async createJob(
    params: TriggerAnalysisParams,
  ): Promise<CreateJobResponse> {
    return apiPost<CreateJobResponse>('/api/analysis/jobs', params)
  },

  /** Absolute URL for the SSE stream; consumed by native EventSource. */
  streamUrl(jobId: string): string {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    return `${base}/api/analysis/stream/${encodeURIComponent(jobId)}`
  },
}
