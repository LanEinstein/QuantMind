/** API client for risk control center endpoints. */

import { apiGet, apiPost } from './request'
import type {
  RiskStatus,
  RiskConfig,
  RiskEvent,
  RiskRadarData,
  AuthorizationMode,
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

  updateConfig(config: Partial<RiskConfig>): Promise<RiskConfig> {
    return apiPost<RiskConfig>('/api/risk/config', config)
  },

  getEvents(params: {
    level?: string
    start_date?: string
    end_date?: string
    limit?: number
  } = {}): Promise<RiskEvent[]> {
    return apiGet<RiskEvent[]>(
      '/api/risk/events',
      params as Record<string, unknown>,
    )
  },

  switchAuthMode(mode: AuthorizationMode): Promise<RiskStatus> {
    return apiPost<RiskStatus>('/api/risk/auth-mode', { mode })
  },
}
