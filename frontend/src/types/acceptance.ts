/** Front-end mirror of backend AcceptanceReport payload (G-007). */

export type AcceptanceOutcome =
  | 'PASS'
  | 'FAIL'
  | 'PAUSED'
  | 'INSUFFICIENT_DATA'

export type MetricDirection = 'at_least' | 'at_most'

export interface AcceptanceMetricRow {
  readonly name: string
  readonly value: number
  readonly threshold: number
  readonly direction: MetricDirection
  readonly passed: boolean
}

export interface AcceptanceReportSnapshot {
  readonly report_id: string
  readonly computed_at: string
  readonly trade_date: string
  readonly window_start: string
  readonly window_end: string
  readonly trading_days_in_window: number
  readonly outcome: AcceptanceOutcome
  readonly metrics: readonly AcceptanceMetricRow[]
  readonly notes: string
}

export interface AcceptanceLatestPayload {
  readonly report: AcceptanceReportSnapshot | null
  readonly can_switch_to_feishu_on: boolean
  readonly service_status: 'ok' | 'unavailable'
  readonly timestamp?: string
}

/**
 * Locked P0-6 §1 metric ordering used by the AcceptanceReports view.
 * Five stability rows + three strategy rows. Frontend table renders in
 * this order so operators see the same row sequence every day.
 */
export const ACCEPTANCE_METRIC_ORDER: readonly string[] = [
  'instruction_completion_rate',
  'execution_report_accuracy_rate',
  'data_missing_rate',
  'llm_timeout_rate',
  'signal_generation_rate',
  'max_drawdown_pct',
  'pnl_cny',
  'csi300_excess_pct',
]

export const METRIC_LABELS: Record<string, string> = {
  instruction_completion_rate: '指令完成率',
  execution_report_accuracy_rate: '回报准确率',
  data_missing_rate: '数据缺失率',
  llm_timeout_rate: 'LLM 超时率',
  signal_generation_rate: '信号生成率',
  max_drawdown_pct: '最大回撤',
  pnl_cny: '累计 PnL',
  csi300_excess_pct: '沪深 300 超额',
}
