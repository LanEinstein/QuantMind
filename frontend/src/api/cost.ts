/** G-008 — Read-only cost-breakdown + budget API client. */

import { apiGet } from './request'

export interface CostBudgetState {
  daily_budget: number
  spent_today: number
  soft_ceiling: number
  hard_ceiling: number
  remaining: number
  status: 'ok' | 'soft_breach' | 'hard_breach'
}

export interface CostMonthlyBudgetState {
  monthly_budget: number
  spent_month: number
  fraction: number
  threshold_reached: number | null
  status: 'ok' | 'threshold_50' | 'threshold_80' | 'threshold_100'
}

export interface CostKimiBudgetState {
  kimi_daily_cap: number
  spent_today: number
  remaining: number
  status: 'ok' | 'hard_breach'
}

export interface CostBudgetPayload {
  status: 'ok' | 'unavailable'
  reason?: string
  daily?: CostBudgetState
  monthly?: CostMonthlyBudgetState
  kimi?: CostKimiBudgetState
  timestamp?: string
}

export interface CostBreakdownPayload {
  status: 'ok' | 'unavailable'
  reason?: string
  days?: number
  total_cost_rmb?: number
  daily_totals?: Record<string, number>
  by_provider?: Record<string, number>
  by_provider_daily?: Record<string, Record<string, number>>
  timestamp?: string
}

export interface CostSoftDegradePayload {
  status: 'ok' | 'unavailable'
  reason?: string
  kimi_escalation_blocked?: boolean
  daily_status?: string
  monthly_status?: string
  kimi_status?: string
  monthly_threshold_reached?: number | null
  timestamp?: string
}

export const costApi = {
  budget(): Promise<CostBudgetPayload> {
    return apiGet<CostBudgetPayload>('/api/cost/budget')
  },

  breakdown(days = 7): Promise<CostBreakdownPayload> {
    return apiGet<CostBreakdownPayload>('/api/cost/breakdown', { days })
  },

  softDegrade(): Promise<CostSoftDegradePayload> {
    return apiGet<CostSoftDegradePayload>('/api/cost/soft-degrade')
  },
}
