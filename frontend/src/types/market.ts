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

export interface WsMessage {
  type:
    | 'index_update'
    | 'stock_update'
    | 'news'
    | 'signal'
    | 'status'
    | 'position_update'
    | 'circuit_breaker_update'
  data: unknown
}
