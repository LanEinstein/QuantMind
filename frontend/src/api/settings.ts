/** API client for system settings endpoints. */

import { apiGet, apiPost } from './request'
import type {
  LLMConfig,
  ConnectionTestResult,
  DataSourceStatus,
  MiroFishConfig,
  CostSummary,
} from '@/types/settings'

export const settingsApi = {
  getLLMConfig(): Promise<LLMConfig> {
    return apiGet<LLMConfig>('/api/settings/llm-config')
  },

  updateLLMConfig(data: Record<string, unknown>): Promise<LLMConfig> {
    return apiPost<LLMConfig>('/api/settings/llm-config', data)
  },

  testLLMProvider(provider: string): Promise<ConnectionTestResult> {
    return apiPost<ConnectionTestResult>(
      '/api/settings/llm-config/test',
      { provider },
    )
  },

  getDataSources(): Promise<DataSourceStatus[]> {
    return apiGet<DataSourceStatus[]>('/api/settings/data-sources')
  },

  testDataSource(source: string): Promise<DataSourceStatus> {
    return apiPost<DataSourceStatus>(
      '/api/settings/data-sources/test',
      { source },
    )
  },

  getMiroFishConfig(): Promise<MiroFishConfig> {
    return apiGet<MiroFishConfig>('/api/settings/mirofish')
  },

  updateMiroFishConfig(data: Record<string, unknown>): Promise<MiroFishConfig> {
    return apiPost<MiroFishConfig>('/api/settings/mirofish', data)
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
