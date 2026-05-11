/** API client for trading/portfolio endpoints (GET-only per P1-5 §2). */

import { apiGet } from './request'
import type {
  AccountMeta,
  AccountInfo,
  PositionItem,
  OrderItem,
  TradeItem,
  CircuitBreakerStatus,
} from '@/types/trading'

export const tradingApi = {
  getAccounts: () => apiGet<AccountMeta[]>('/api/trading/accounts'),

  getAccount: (accountId = 'default') =>
    apiGet<AccountInfo>('/api/trading/account', { account_id: accountId }),

  getPositions: (accountId = 'default') =>
    apiGet<PositionItem[]>('/api/trading/positions', { account_id: accountId }),

  getOrders: (accountId = 'default', status?: string) =>
    apiGet<OrderItem[]>('/api/trading/orders', {
      account_id: accountId,
      ...(status ? { status } : {}),
    }),

  getTrades: (params: {
    account_id?: string
    code?: string
    start_date?: string
    end_date?: string
  } = {}) => apiGet<TradeItem[]>('/api/trading/trades', params as Record<string, unknown>),

  getCircuitBreakerStatus: () =>
    apiGet<CircuitBreakerStatus>('/api/trading/circuit-breaker-status'),
}
