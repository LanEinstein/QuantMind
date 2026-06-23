/** Z-003 / AF-007 — read-only position-thesis API client. */

import { apiGet } from './request'
import type { PositionThesesPayload } from '@/types/positionThesis'

export const positionThesesApi = {
  /**
   * Fetch the open position theses, optionally filtered to one sleeve's holds
   * (AF-007: `value` / `short_term`). Read-only; the backend filter is a pure
   * predicate on the persisted style label.
   */
  list(style?: string): Promise<PositionThesesPayload> {
    const params = style ? { style } : undefined
    return apiGet<PositionThesesPayload>('/api/position-theses', params)
  },
}
