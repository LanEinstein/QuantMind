/** API client for system settings endpoints (GET-only per P1-5 §2). */

import { apiGet } from './request'
import type {
  LLMConfig,
  DataSourceStatus,
  MiroFishConfig,
  CostSummary,
} from '@/types/settings'

export const settingsApi = {
  getLLMConfig(): Promise<LLMConfig> {
    return apiGet<LLMConfig>('/api/settings/llm-config')
  },

  getDataSources(): Promise<DataSourceStatus[]> {
    return apiGet<DataSourceStatus[]>('/api/settings/data-sources')
  },

  getMiroFishConfig(): Promise<MiroFishConfig> {
    return apiGet<MiroFishConfig>('/api/settings/mirofish')
  },

  getCostStats(params?: {
    period?: string
    days?: number
  }): Promise<CostSummary> {
    return apiGet<CostSummary>(
      '/api/settings/cost-stats',
      params as Record<string, unknown>,
    )
  },
}
