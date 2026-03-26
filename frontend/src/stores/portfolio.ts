/** Pinia store for portfolio / virtual trading state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  AccountMeta,
  AccountInfo,
  PositionItem,
  OrderItem,
  TradeItem,
  PendingApproval,
  PortfolioStatus,
} from '@/types/trading'
import { tradingApi } from '@/api/trading'

export const usePortfolioStore = defineStore('portfolio', () => {
  // --- State ---
  const accounts = ref<AccountMeta[]>([])
  const activeAccountId = ref('default')
  const account = ref<AccountInfo | null>(null)
  const positions = ref<PositionItem[]>([])
  const orders = ref<OrderItem[]>([])
  const trades = ref<TradeItem[]>([])
  const pendingApprovals = ref<PendingApproval[]>([])
  const status = ref<PortfolioStatus>('idle')
  const tradeFilterCode = ref('')
  const tradeFilterDateRange = ref<[string, string] | null>(null)

  const isDev = import.meta.env.DEV

  // --- Computed ---
  const hasPendingApprovals = computed(() => pendingApprovals.value.length > 0)

  const positionRatio = computed(() => {
    if (!account.value || account.value.total_assets <= 0) return 0
    return Math.round((account.value.market_value / account.value.total_assets) * 100)
  })

  const cashRatio = computed(() => {
    if (!account.value || account.value.total_assets <= 0) return 0
    return Math.round((account.value.available_cash / account.value.total_assets) * 100)
  })

  const filteredTrades = computed(() => {
    let result = trades.value
    if (tradeFilterCode.value) {
      result = result.filter((t) => t.code.includes(tradeFilterCode.value))
    }
    if (tradeFilterDateRange.value) {
      const [start, end] = tradeFilterDateRange.value
      // Append time boundaries for inclusive day comparison
      const startISO = start.includes('T') ? start : start + 'T00:00:00'
      const endISO = end.includes('T') ? end : end + 'T23:59:59'
      result = result.filter((t) => {
        const d = t.traded_at
        return d >= startISO && d <= endISO
      })
    }
    return result
  })

  // --- Actions ---
  async function fetchAccounts(): Promise<boolean> {
    try {
      accounts.value = await tradingApi.getAccounts()
      return true
    } catch {
      console.warn('Failed to fetch accounts, using mock data')
      if (isDev) accounts.value = mockAccounts()
      return isDev
    }
  }

  async function fetchAccount(accountId?: string): Promise<boolean> {
    const id = accountId ?? activeAccountId.value
    try {
      account.value = await tradingApi.getAccount(id)
      return true
    } catch {
      console.warn('Failed to fetch account, using mock data')
      if (isDev) account.value = mockAccountInfo()
      return isDev
    }
  }

  async function fetchPositions(accountId?: string): Promise<boolean> {
    const id = accountId ?? activeAccountId.value
    try {
      positions.value = await tradingApi.getPositions(id)
      return true
    } catch {
      console.warn('Failed to fetch positions, using mock data')
      if (isDev) positions.value = mockPositions()
      return isDev
    }
  }

  async function fetchOrders(accountId?: string): Promise<boolean> {
    const id = accountId ?? activeAccountId.value
    try {
      orders.value = await tradingApi.getOrders(id)
      return true
    } catch {
      console.warn('Failed to fetch orders, using mock data')
      if (isDev) orders.value = mockOrders()
      return isDev
    }
  }

  async function fetchTrades(accountId?: string): Promise<boolean> {
    const id = accountId ?? activeAccountId.value
    try {
      trades.value = await tradingApi.getTrades({ account_id: id })
      return true
    } catch {
      console.warn('Failed to fetch trades, using mock data')
      if (isDev) trades.value = mockTrades()
      return isDev
    }
  }

  async function fetchPendingApprovals(accountId?: string): Promise<boolean> {
    const id = accountId ?? activeAccountId.value
    try {
      pendingApprovals.value = await tradingApi.getPendingApprovals(id)
      return true
    } catch {
      console.warn('Failed to fetch pending approvals, using mock data')
      if (isDev) pendingApprovals.value = mockPendingApprovals()
      return isDev
    }
  }

  async function fetchAll() {
    status.value = 'loading'
    const id = activeAccountId.value
    const results = await Promise.all([
      fetchAccounts(),
      fetchAccount(id),
      fetchPositions(id),
      fetchOrders(id),
      fetchTrades(id),
      fetchPendingApprovals(id),
    ])
    const allFailed = results.every((ok) => !ok)
    status.value = allFailed ? 'error' : 'loaded'
  }

  async function switchAccount(accountId: string) {
    activeAccountId.value = accountId
    await fetchAll()
  }

  async function cancelOrder(orderId: string) {
    await tradingApi.cancelOrder(orderId, activeAccountId.value)
    await Promise.allSettled([fetchAccount(), fetchPositions(), fetchOrders()])
  }

  async function approveOrder(id: string) {
    const result = await tradingApi.approveOrder(id)
    // Always refresh state — backend removes the pending item regardless of broker outcome
    await Promise.allSettled([
      fetchAccount(),
      fetchPositions(),
      fetchOrders(),
      fetchTrades(),
      fetchPendingApprovals(),
    ])
    if (!result.success) {
      throw new Error(result.message || '订单被拒绝')
    }
  }

  async function rejectOrder(id: string) {
    await tradingApi.rejectOrder(id)
    await fetchPendingApprovals()
  }

  function exportTradesCSV() {
    const rows = filteredTrades.value
    if (rows.length === 0) return

    const header = '时间,代码,方向,价格,数量,金额,佣金,印花税\n'
    const body = rows
      .map(
        (t) =>
          `${t.traded_at},${t.code},${t.direction === 'BUY' ? '买入' : '卖出'},` +
          `${t.price},${t.volume},${t.amount.toFixed(2)},` +
          `${t.commission.toFixed(2)},${t.stamp_tax.toFixed(2)}`,
      )
      .join('\n')

    const blob = new Blob(['\uFEFF' + header + body], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `trades_${activeAccountId.value}_${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return {
    accounts,
    activeAccountId,
    account,
    positions,
    orders,
    trades,
    pendingApprovals,
    status,
    tradeFilterCode,
    tradeFilterDateRange,
    hasPendingApprovals,
    positionRatio,
    cashRatio,
    filteredTrades,
    fetchAccounts,
    fetchAccount,
    fetchPositions,
    fetchOrders,
    fetchTrades,
    fetchPendingApprovals,
    fetchAll,
    switchAccount,
    cancelOrder,
    approveOrder,
    rejectOrder,
    exportTradesCSV,
  }
})

// --- Mock data (deterministic, no Math.random) ---

function mockAccounts(): AccountMeta[] {
  return [
    { account_id: 'default', label: '策略A (默认)', created_at: '2026-03-01T08:00:00Z' },
    { account_id: 'conservative', label: '策略B (保守)', created_at: '2026-03-05T08:00:00Z' },
  ]
}

function mockAccountInfo(): AccountInfo {
  return {
    total_assets: 1032450.0,
    available_cash: 206450.0,
    frozen_cash: 0.0,
    market_value: 826000.0,
    total_pnl: 32450.0,
    total_pnl_pct: 0.032450,
    initial_capital: 1000000.0,
  }
}

function mockPositions(): PositionItem[] {
  return [
    {
      code: '600519',
      volume: 100,
      available_volume: 100,
      cost_price: 1680.0,
      market_value: 173000.0,
      unrealized_pnl: 5000.0,
      unrealized_pnl_pct: 0.0298,
      stop_loss_line: 1545.6,
      stop_loss_distance: 0.08,
      position_pct: 0.1676,
      risk_status: 'normal',
    },
    {
      code: '000001',
      volume: 5000,
      available_volume: 5000,
      cost_price: 11.20,
      market_value: 55000.0,
      unrealized_pnl: -1000.0,
      unrealized_pnl_pct: -0.0179,
      stop_loss_line: 10.304,
      stop_loss_distance: 0.08,
      position_pct: 0.0533,
      risk_status: 'normal',
    },
    {
      code: '300750',
      volume: 2000,
      available_volume: 2000,
      cost_price: 210.0,
      market_value: 418000.0,
      unrealized_pnl: -2000.0,
      unrealized_pnl_pct: -0.0048,
      stop_loss_line: 193.2,
      stop_loss_distance: 0.08,
      position_pct: 0.4049,
      risk_status: 'over_limit',
    },
    {
      code: '601318',
      volume: 3000,
      available_volume: 3000,
      cost_price: 52.30,
      market_value: 156900.0,
      unrealized_pnl: -300.0,
      unrealized_pnl_pct: -0.0019,
      stop_loss_line: 48.116,
      stop_loss_distance: 0.035,
      position_pct: 0.152,
      risk_status: 'near_stop',
    },
  ]
}

const STOCK_NAMES: Record<string, string> = {
  '600519': '贵州茅台',
  '000001': '平安银行',
  '300750': '宁德时代',
  '601318': '中国平安',
  '000858': '五粮液',
  '002594': '比亚迪',
}

export function getStockName(code: string): string {
  return STOCK_NAMES[code] ?? code
}

function mockOrders(): OrderItem[] {
  return [
    {
      order_id: 'ord-001',
      code: '600519',
      price: 1730.0,
      volume: 100,
      filled_volume: 100,
      avg_fill_price: 1730.04,
      direction: 'BUY',
      order_type: 'LIMIT',
      status: 'FILLED',
      created_at: '2026-03-25T09:35:00Z',
      updated_at: '2026-03-25T09:35:01Z',
      reject_reason: null,
    },
    {
      order_id: 'ord-002',
      code: '000001',
      price: 11.0,
      volume: 1000,
      filled_volume: 0,
      avg_fill_price: 0,
      direction: 'BUY',
      order_type: 'LIMIT',
      status: 'PENDING',
      created_at: '2026-03-25T10:15:00Z',
      updated_at: '2026-03-25T10:15:00Z',
      reject_reason: null,
    },
    {
      order_id: 'ord-003',
      code: '601318',
      price: 53.0,
      volume: 500,
      filled_volume: 0,
      avg_fill_price: 0,
      direction: 'SELL',
      order_type: 'LIMIT',
      status: 'CANCELLED',
      created_at: '2026-03-25T09:40:00Z',
      updated_at: '2026-03-25T09:45:00Z',
      reject_reason: null,
    },
    {
      order_id: 'ord-004',
      code: '300750',
      price: 205.0,
      volume: 200,
      filled_volume: 0,
      avg_fill_price: 0,
      direction: 'SELL',
      order_type: 'LIMIT',
      status: 'REJECTED',
      created_at: '2026-03-25T09:31:00Z',
      updated_at: '2026-03-25T09:31:01Z',
      reject_reason: 'Insufficient available shares (T+1)',
    },
  ]
}

function mockTrades(): TradeItem[] {
  return [
    {
      trade_id: 'trd-001',
      order_id: 'ord-001',
      code: '600519',
      price: 1730.04,
      volume: 100,
      amount: 173004.0,
      direction: 'BUY',
      commission: 51.90,
      stamp_tax: 0,
      slippage_cost: 4.0,
      net_amount: 173055.9,
      traded_at: '2026-03-25T09:35:01Z',
    },
    {
      trade_id: 'trd-002',
      order_id: 'ord-prev-001',
      code: '601318',
      price: 52.28,
      volume: 3000,
      amount: 156840.0,
      direction: 'BUY',
      commission: 47.05,
      stamp_tax: 0,
      slippage_cost: 31.37,
      net_amount: 156887.05,
      traded_at: '2026-03-24T14:20:00Z',
    },
    {
      trade_id: 'trd-003',
      order_id: 'ord-prev-002',
      code: '000858',
      price: 148.95,
      volume: 200,
      amount: 29790.0,
      direction: 'SELL',
      commission: 8.94,
      stamp_tax: 29.79,
      slippage_cost: 5.96,
      net_amount: 29751.27,
      traded_at: '2026-03-24T10:05:00Z',
    },
  ]
}

function mockPendingApprovals(): PendingApproval[] {
  return [
    {
      id: 'apv-001',
      account_id: 'default',
      code: '601318',
      price: 52.3,
      volume: 300,
      direction: 'BUY',
      order_type: 'LIMIT',
      agent_recommendation: '基金经理建议买入 601318中国平安 300股@¥52.3',
      reasoning:
        '基本面分析显示中国平安当前PE处于历史低位，保险业务复苏趋势明确，预计未来两个季度EPS增长15%。',
      risk_pre_check: { passed: true, rule_name: 'all_checks', message: '风控检查: 通过' },
      created_at: '2026-03-25T10:30:00Z',
    },
    {
      id: 'apv-002',
      account_id: 'default',
      code: '300750',
      price: 209.0,
      volume: 500,
      direction: 'BUY',
      order_type: 'LIMIT',
      agent_recommendation: '基金经理建议买入 300750宁德时代 500股@¥209.0',
      reasoning: '新能源汽车补贴政策延续利好电池龙头，订单量环比增长20%。',
      risk_pre_check: {
        passed: false,
        rule_name: 'position_limit',
        message: '仓位将达18%，接近单股上限20%',
      },
      created_at: '2026-03-25T10:32:00Z',
    },
  ]
}
