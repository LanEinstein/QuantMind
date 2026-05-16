/** TypeScript interfaces matching backend Pydantic models. */

export interface IndexQuote {
  code: string
  name: string
  price: number
  change_pct: number
  volume: number
  amount: number
  timestamp: string
}

export interface StockQuote {
  code: string
  name: string
  price: number
  open: number
  high: number
  low: number
  prev_close: number
  change_pct: number
  volume: number
  amount: number
  turnover_rate: number
  timestamp: string
}

export interface SectorQuote {
  name: string
  change_pct: number
  leader_code: string
  leader_name: string
  leader_change_pct: number
  timestamp: string
}

export interface CapitalFlowData {
  north_net_inflow: number
  main_net_inflow: number
  timestamp: string
}

export interface FinancialData {
  code: string
  name: string
  pe_ratio: number | null
  pb_ratio: number | null
  roe: number | null
  eps: number | null
  revenue_growth: number | null
  report_date: string
  timestamp: string
}

export interface NewsArticle {
  title: string
  content: string
  source: string
  url: string
  publish_time: string
  stock_codes: string[]
  importance_score: number
  has_simulation?: boolean
  simulation_summary?: string
}

export interface ApiEnvelope<T> {
  status: 'ok' | 'error'
  data: T
  error: string | null
}

export interface SystemStatus {
  deepseek: boolean
  qwen: boolean
  kimi: boolean
  adata: boolean
  akshare: boolean
  daily_cost_rmb: number
  risk_status: 'normal' | 'warning' | 'halt'
  /** P0-1: feishu_interactive overlay flag (simulation_auto is always on). */
  feishu_interactive: boolean
}

export interface MarketStats {
  rising: number
  falling: number
  flat: number
  limit_up: number
  limit_down: number
}

/**
 * G-009 — locked 14-kind WebSocket message union.
 *
 * 6 retained from the legacy WS contract:
 *   index_update, signal, news, status, position_update,
 *   circuit_breaker_update.
 *
 * 8 added by G-009 — sent via :data:`CHANNEL_SYSTEM`:
 *   instruction_plan_update, broker_event, equity_point_update,
 *   data_quality_breach, freeze_source_update, ticket_update,
 *   acceptance_report_ready, feishu_message_received.
 *
 * 2 forbidden (P1-5 §2 红线 4 — removed by G-009):
 *   auth_mode_change, approval_update.
 *
 * SSE stays LLM-only (P1-5 §2 红线 4).
 */
export type WsMessageType =
  // 6 retained
  | 'index_update'
  | 'signal'
  | 'news'
  | 'status'
  | 'position_update'
  | 'circuit_breaker_update'
  // 8 new (G-009)
  | 'instruction_plan_update'
  | 'broker_event'
  | 'equity_point_update'
  | 'data_quality_breach'
  | 'freeze_source_update'
  | 'ticket_update'
  | 'acceptance_report_ready'
  | 'feishu_message_received'

export const WS_MESSAGE_TYPES: readonly WsMessageType[] = [
  'index_update',
  'signal',
  'news',
  'status',
  'position_update',
  'circuit_breaker_update',
  'instruction_plan_update',
  'broker_event',
  'equity_point_update',
  'data_quality_breach',
  'freeze_source_update',
  'ticket_update',
  'acceptance_report_ready',
  'feishu_message_received',
] as const

/**
 * Removed by G-009 — these strings MUST NOT appear in the WS bridge
 * (defense in depth: backend ``FORBIDDEN_WS_TYPES`` mirrors this list).
 */
export const FORBIDDEN_WS_MESSAGE_TYPES: readonly string[] = [
  'auth_mode_change',
  'approval_update',
] as const

export interface WsMessage {
  type: WsMessageType
  data: unknown
}
