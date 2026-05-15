/** API client for the AcceptanceReports view (G-007). */

import { apiGet } from './request'
import type { AcceptanceLatestPayload } from '@/types/acceptance'

export const acceptanceApi = {
  getLatest(): Promise<AcceptanceLatestPayload> {
    return apiGet<AcceptanceLatestPayload>('/api/acceptance/latest')
  },
}
