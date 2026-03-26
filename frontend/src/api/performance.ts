/** API client for performance analytics endpoints. */

import { apiGet } from './request'
import type { PerformanceData } from '@/types/performance'

export const performanceApi = {
  getData(params: {
    start?: string
    end?: string
    benchmark?: string
    account_id?: string
  } = {}): Promise<PerformanceData> {
    return apiGet<PerformanceData>(
      '/api/performance',
      params as Record<string, unknown>,
    )
  },

  exportReport(type: 'daily' | 'weekly' | 'monthly'): Promise<Blob> {
    return apiGet<Blob>(`/api/performance/export/${type}`)
  },
}
