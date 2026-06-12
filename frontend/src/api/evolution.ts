/** API client for the X-021 evolution GET endpoints (P1-5 §2 红线 1+2). */

import { apiGet } from './request'
import type {
  EvolutionHistoryPayload,
  EvolutionPendingPayload,
} from '@/types/evolution'

export const evolutionApi = {
  /**
   * Snapshot of ``docs/decisions/pending/`` — drafted amendments
   * awaiting operator review.
   *
   * Returns ``{count, items, yellow_threshold, red_threshold, ...}``
   * so the SystemStatus.vue card can render the right colour band
   * without hard-coding the thresholds.
   */
  getPending(): Promise<EvolutionPendingPayload> {
    return apiGet<EvolutionPendingPayload>('/api/evolution/pending')
  },

  /**
   * AD-003 — experiment registry (failures included) + promotion-intent
   * history + the current activation manifest. Read-only; degrades to an
   * empty shape when Mongo is unwired.
   */
  getHistory(limit = 50): Promise<EvolutionHistoryPayload> {
    return apiGet<EvolutionHistoryPayload>('/api/evolution/history', { limit })
  },
}
