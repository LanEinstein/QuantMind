/** Z-003 — read-only position-thesis API client. */

import { apiGet } from './request'
import type { PositionThesesPayload } from '@/types/positionThesis'

export const positionThesesApi = {
  list(): Promise<PositionThesesPayload> {
    return apiGet<PositionThesesPayload>('/api/position-theses')
  },
}
