/** API client for the EquityPoint MTM snapshot (G-004). */

import { apiGet } from './request'
import type { EquityPointLatestPayload } from '@/types/equityPoint'

export const equityPointsApi = {
  getLatest(): Promise<EquityPointLatestPayload> {
    return apiGet<EquityPointLatestPayload>(
      '/api/portfolio/equity-points/latest',
    )
  },
}
