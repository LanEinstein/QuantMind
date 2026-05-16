/** G-006 — Reconciliation API client (list + decide). */

import { apiGet, apiPost } from './request'

export type ReconciliationTicketStatus =
  | 'OPEN'
  | 'RESOLVED_USER_AS_TRUTH'
  | 'RESOLVED_SYSTEM_AS_TRUTH'
  | 'RESOLVED_AMENDED'
  | 'EXPIRED'

export interface FieldDeviation {
  field: string
  expected: string
  actual: string
  abs_diff: number
  threshold: number
  passed: boolean
}

export interface DeviationReport {
  ticket_id: string
  overall_passed: boolean
  deviations: FieldDeviation[]
}

export interface ReportedPosition {
  code: string
  volume: number
  cost_price: number
}

export interface MockBrokerSnapshot {
  cash: number
  snapshot_at: string
  positions: ReportedPosition[]
}

export interface ReconciliationTicket {
  ticket_id: string
  trade_date: string
  created_at: string
  status: ReconciliationTicketStatus
  resolved_at: string | null
  resolution_message_id: string | null
  expected_snapshot_id: string
  actual_reconciliation_id: string
  deviation_report: DeviationReport
  amended_snapshot: MockBrokerSnapshot | null
}

export interface ReconciliationTicketListPayload {
  status: 'ok' | 'unavailable'
  trade_date: string | null
  tickets: ReconciliationTicket[]
  count?: number
  timestamp?: string
}

export interface DecisionApplyResult {
  cash_delta: number
  positions_delta: Array<Record<string, unknown>>
  broker_event_sequence: number | null
  reason: string
}

export interface DecisionResultPayload {
  ticket_id: string
  status: ReconciliationTicketStatus
  apply_result: DecisionApplyResult
}

export interface DecideBody {
  resolution:
    | 'RESOLVED_USER_AS_TRUTH'
    | 'RESOLVED_SYSTEM_AS_TRUTH'
    | 'RESOLVED_AMENDED'
  amended_snapshot?: MockBrokerSnapshot | null
  resolution_message_id?: string | null
  actor_detail?: string | null
}

export const reconciliationApi = {
  list(tradeDate?: string): Promise<ReconciliationTicketListPayload> {
    const params: Record<string, unknown> = {}
    if (tradeDate) params.trade_date = tradeDate
    return apiGet<ReconciliationTicketListPayload>(
      '/api/reconciliation-tickets',
      params,
    )
  },

  decide(
    ticketId: string,
    body: DecideBody,
  ): Promise<DecisionResultPayload> {
    return apiPost<DecisionResultPayload>(
      `/api/reconciliation-tickets/${encodeURIComponent(ticketId)}/decide`,
      body,
    )
  },
}
