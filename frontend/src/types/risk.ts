/** TypeScript interfaces for the risk control center subsystem. */

export type SystemStatusLevel = 'normal' | 'warning' | 'circuit_breaker'

export interface RunMode {
  readonly simulation_auto: boolean
  readonly feishu_interactive: boolean
}

export interface RiskStatus {
  readonly system_status: SystemStatusLevel
  readonly run_mode: RunMode
  readonly stop_loss_triggers_today: number
  readonly circuit_breaker_triggered: boolean
  readonly llm_intercepts_today: number
}

export interface RiskRadarData {
  readonly total_position_pct: number
  readonly total_position_limit: number
  readonly max_single_stock_pct: number
  readonly max_single_stock_limit: number
  readonly industry_concentration_pct: number
  readonly industry_concentration_limit: number
  readonly daily_loss_pct: number
  readonly daily_loss_limit: number
  readonly stock_count: number
  readonly stock_count_limit: number
}

export interface RiskConfig {
  readonly single_stock_limit: number
  readonly total_position_limit: number
  readonly stop_loss_threshold: number
  readonly circuit_breaker_threshold: number
  readonly llm_timeout_seconds: number
  readonly llm_max_consecutive_failures: number
  readonly price_deviation_limit: number
}

export type RiskEventLevel = 'info' | 'warning' | 'critical' | 'success'

export interface RiskEvent {
  readonly id: string
  readonly timestamp: string
  readonly level: RiskEventLevel
  readonly description: string
  readonly action_taken: string
}

export type RiskStoreStatus = 'idle' | 'loading' | 'loaded' | 'error'
