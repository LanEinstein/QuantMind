/** API client for MiroFish simulation endpoints. */

import { apiGet } from './request'
import type {
  SimulationResult,
  SimulationHistoryItem,
  SimulationComparison,
} from '@/types/simulation'

export const simulationApi = {
  async getLatest(): Promise<SimulationResult> {
    return apiGet<SimulationResult>('/api/simulation/latest')
  },

  async getById(id: string): Promise<SimulationResult> {
    return apiGet<SimulationResult>(`/api/simulation/${encodeURIComponent(id)}`)
  },

  async getHistory(params?: {
    search?: string
    limit?: number
  }): Promise<SimulationHistoryItem[]> {
    return apiGet<SimulationHistoryItem[]>(
      '/api/simulation/history',
      params as Record<string, unknown>,
    )
  },

  async compare(aId: string, bId: string): Promise<SimulationComparison> {
    return apiGet<SimulationComparison>('/api/simulation/compare', {
      a: aId,
      b: bId,
    })
  },
}
