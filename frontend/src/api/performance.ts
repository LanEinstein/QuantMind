/** API client for performance analytics endpoints. */

import { apiGet, apiDownload } from './request'
import type { EquityKpisPayload, PerformanceData } from '@/types/performance'

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

  /** AD-001 — EquityPoint-sourced KPI header + segmented equity series. */
  getEquityKpis(params: {
    start?: string
    end?: string
    benchmark?: string
  } = {}): Promise<EquityKpisPayload> {
    return apiGet<EquityKpisPayload>(
      '/api/performance/equity-kpis',
      params as Record<string, unknown>,
    )
  },

  exportReport(type: 'daily' | 'weekly' | 'monthly'): Promise<Blob> {
    return apiDownload(`/api/performance/export/${type}`)
  },
}
