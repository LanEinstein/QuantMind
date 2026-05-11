/** API client for risk control center endpoints (GET-only per P1-5 §2). */

import { apiGet } from './request'
import type {
  RiskStatus,
  RiskConfig,
  RiskEvent,
  RiskEventLevel,
  RiskRadarData,
} from '@/types/risk'

export const riskApi = {
  getStatus(): Promise<RiskStatus> {
    return apiGet<RiskStatus>('/api/risk/status')
  },

  getRadarData(): Promise<RiskRadarData> {
    return apiGet<RiskRadarData>('/api/risk/radar')
  },

  getConfig(): Promise<RiskConfig> {
    return apiGet<RiskConfig>('/api/risk/config')
  },

  getEvents(params: {
    level?: RiskEventLevel
    start_date?: string
    end_date?: string
    limit?: number
  } = {}): Promise<RiskEvent[]> {
    return apiGet<RiskEvent[]>(
      '/api/risk/events',
      params as Record<string, unknown>,
    )
  },
}
