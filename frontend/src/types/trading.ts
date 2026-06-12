/** TypeScript interfaces for the trading/portfolio subsystem. */

export interface AccountMeta {
  readonly account_id: string
  readonly label: string
  readonly created_at: string
}

export interface AccountInfo {
  readonly total_assets: number
  readonly available_cash: number
  readonly frozen_cash: number
  readonly market_value: number
  readonly total_pnl: number
  readonly total_pnl_pct: number
  readonly initial_capital: number
}

export type RiskStatusLevel = 'normal' | 'near_stop' | 'triggered' | 'over_limit'

export interface PositionItem {
  readonly code: string
  readonly volume: number
  readonly available_volume: number
  readonly cost_price: number
  readonly market_value: number
  readonly unrealized_pnl: number
  readonly unrealized_pnl_pct: number
  readonly stop_loss_line: number
  readonly stop_loss_distance: number
  readonly position_pct: number
  readonly risk_status: RiskStatusLevel
  /** AD-004 — deterministic buy-time style nameplate (AC-001), display-only.
   * `null`/absent on legacy + reconciliation-reset positions. */
  readonly entry_style?: string | null
}

export type OrderStatusType = 'PENDING' | 'FILLED' | 'CANCELLED' | 'REJECTED'

export interface OrderItem {
  readonly order_id: string
  readonly code: string
  readonly price: number
  readonly volume: number
  readonly filled_volume: number
  readonly avg_fill_price: number
  readonly direction: 'BUY' | 'SELL'
  readonly order_type: 'LIMIT' | 'MARKET'
  readonly status: OrderStatusType
  readonly created_at: string
  readonly updated_at: string
  readonly reject_reason: string | null
}

export interface TradeItem {
  readonly trade_id: string
  readonly order_id: string
  readonly code: string
  readonly price: number
  readonly volume: number
  readonly amount: number
  readonly direction: 'BUY' | 'SELL'
  readonly commission: number
  readonly stamp_tax: number
  readonly slippage_cost: number
  /** P1-2.C SZ 0.00341% double-sided 过户费 (non-destructive; defaults 0 on SH-only). */
  readonly transfer_fee?: number
  readonly net_amount: number
  readonly traded_at: string
}

export type PortfolioStatus = 'idle' | 'loading' | 'loaded' | 'error'

export interface CircuitBreakerStatus {
  readonly halted: boolean
  readonly daily_pnl_pct: number
  readonly consecutive_losses: number
}
