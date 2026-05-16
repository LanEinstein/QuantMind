/** G-005 — POST /api/execution-reports client (frontend backup channel). */

import { apiPost } from './request'

export interface ExecutionReportSubmitOutcome {
  success: boolean
  ambiguous: boolean
  instruction_id: string | null
  template_id: string | null
  apply_result: {
    cash_delta: number
    positions_delta: Array<Record<string, unknown>>
    broker_event_sequence: number | null
    reason: string
  } | null
}

export const executionReportsApi = {
  submit(rawText: string): Promise<ExecutionReportSubmitOutcome> {
    return apiPost<ExecutionReportSubmitOutcome>('/api/execution-reports', {
      raw_text: rawText,
    })
  },
}
