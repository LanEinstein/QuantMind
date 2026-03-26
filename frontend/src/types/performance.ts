/** TypeScript interfaces for the performance analytics subsystem. */

export interface EquityPoint {
  readonly date: string
  readonly portfolio: number
  readonly benchmark: number
}

export interface CoreMetrics {
  readonly annualized_return: number
  readonly sharpe_ratio: number
  readonly max_drawdown: number
  readonly win_rate: number
  readonly profit_loss_ratio: number
  readonly monthly_turnover: number
}

export interface DrawdownPoint {
  readonly date: string
  readonly drawdown: number
}

export interface ModelMetric {
  readonly model: string
  readonly label: string
  readonly accuracy_label: string
  readonly accuracy_value: number
  readonly call_label: string
  readonly call_value: number
  readonly call_unit: string
  readonly cost_label: string
  readonly cost_value: number
  readonly cost_unit: string
}

export interface PerformanceData {
  readonly equity_curve: readonly EquityPoint[]
  readonly metrics: CoreMetrics
  readonly drawdown_curve: readonly DrawdownPoint[]
  readonly model_contributions: readonly ModelMetric[]
}

export type TimeRange = 'week' | 'month' | 'quarter' | 'year' | 'custom'

export type BenchmarkType = 'hs300' | 'sz50' | 'cyb' | 'none'

export type PerformanceStatus = 'idle' | 'loading' | 'loaded' | 'error'
