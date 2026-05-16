/** G-008 — Feishu message history API client (audit-derived). */

import { apiGet } from './request'

export interface FeishuAuditRow {
  event_id: string
  timestamp: string
  event_type: string
  actor: string
  actor_detail: string | null
  outcome: string
  resource_id: string | null
  correlation_id: string | null
  payload: Record<string, unknown>
}

export interface FeishuMessagesPayload {
  source: 'mongo' | 'jsonl_fallback'
  events: FeishuAuditRow[]
  count: number
  limit: number
  timestamp: string
}

export interface FeishuEventTypesPayload {
  event_types: string[]
}

export const feishuMessagesApi = {
  list(limit = 50): Promise<FeishuMessagesPayload> {
    return apiGet<FeishuMessagesPayload>('/api/feishu/messages', { limit })
  },

  eventTypes(): Promise<FeishuEventTypesPayload> {
    return apiGet<FeishuEventTypesPayload>('/api/feishu/event-types')
  },
}
