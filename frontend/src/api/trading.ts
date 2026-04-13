/** API client for trading/portfolio endpoints. */

import { apiGet, apiPost } from './request'
import type {
  AccountMeta,
  AccountInfo,
  PositionItem,
  OrderItem,
  TradeItem,
  PendingApproval,
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

  cancelOrder: (orderId: string, accountId = 'default') =>
    apiPost<{ success: boolean; order_id: string }>(
      `/api/trading/cancel/${encodeURIComponent(orderId)}?account_id=${accountId}`,
    ),

  getPendingApprovals: (accountId?: string) =>
    apiGet<PendingApproval[]>(
      '/api/trading/pending-approvals',
      accountId ? { account_id: accountId } : undefined,
    ),

  approveOrder: (id: string) =>
    apiPost<{ order_id: string; success: boolean; message: string }>(
      `/api/trading/approve/${encodeURIComponent(id)}`,
    ),

  rejectOrder: (id: string) =>
    apiPost<{ success: boolean; id: string }>(
      `/api/trading/reject/${encodeURIComponent(id)}`,
    ),

  getCircuitBreakerStatus: () =>
    apiGet<CircuitBreakerStatus>('/api/trading/circuit-breaker-status'),
}
