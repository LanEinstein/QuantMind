import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePortfolioStore } from '@/stores/portfolio'
import type { PositionItem, CircuitBreakerStatus } from '@/types/trading'

// Mock the trading API module
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getAccounts: vi.fn().mockRejectedValue(new Error('mock')),
    getAccount: vi.fn().mockRejectedValue(new Error('mock')),
    getPositions: vi.fn().mockRejectedValue(new Error('mock')),
    getOrders: vi.fn().mockRejectedValue(new Error('mock')),
    getTrades: vi.fn().mockRejectedValue(new Error('mock')),
    getPendingApprovals: vi.fn().mockRejectedValue(new Error('mock')),
    getCircuitBreakerStatus: vi.fn().mockRejectedValue(new Error('mock')),
    cancelOrder: vi.fn(),
    approveOrder: vi.fn(),
    rejectOrder: vi.fn(),
  },
}))

vi.mock('@/api/risk', () => ({
  riskApi: {
    getStatus: vi.fn().mockRejectedValue(new Error('mock')),
  },
}))

describe('Portfolio Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  describe('updatePositionsFromWs', () => {
    it('replaces positions with incoming data', () => {
      const store = usePortfolioStore()
      expect(store.positions).toHaveLength(0)

      const newPositions: PositionItem[] = [
        {
          code: '600519',
          volume: 100,
          available_volume: 100,
          cost_price: 1680,
          market_value: 173000,
          unrealized_pnl: 5000,
          unrealized_pnl_pct: 0.03,
          stop_loss_line: 1545.6,
          stop_loss_distance: 0.08,
          position_pct: 0.17,
          risk_status: 'normal',
        },
      ]
      store.updatePositionsFromWs(newPositions)
      expect(store.positions).toHaveLength(1)
      expect(store.positions[0].code).toBe('600519')
    })

    it('clears positions with empty array', () => {
      const store = usePortfolioStore()
      store.updatePositionsFromWs([
        {
          code: '000001',
          volume: 100,
          available_volume: 100,
          cost_price: 11,
          market_value: 1100,
          unrealized_pnl: 0,
          unrealized_pnl_pct: 0,
          stop_loss_line: 10.12,
          stop_loss_distance: 0.08,
          position_pct: 0.01,
          risk_status: 'normal',
        },
      ])
      expect(store.positions).toHaveLength(1)

      store.updatePositionsFromWs([])
      expect(store.positions).toHaveLength(0)
    })
  })

  describe('updateCircuitBreaker', () => {
    it('sets circuit breaker status', () => {
      const store = usePortfolioStore()
      expect(store.circuitBreakerStatus).toBeNull()

      const status: CircuitBreakerStatus = {
        halted: true,
        daily_pnl_pct: -0.06,
        consecutive_losses: 3,
      }
      store.updateCircuitBreaker(status)
      expect(store.circuitBreakerStatus?.halted).toBe(true)
      expect(store.circuitBreakerStatus?.daily_pnl_pct).toBe(-0.06)
      expect(store.circuitBreakerStatus?.consecutive_losses).toBe(3)
    })

    it('can transition from halted to not halted', () => {
      const store = usePortfolioStore()
      store.updateCircuitBreaker({ halted: true, daily_pnl_pct: -0.06, consecutive_losses: 3 })
      expect(store.circuitBreakerStatus?.halted).toBe(true)

      store.updateCircuitBreaker({ halted: false, daily_pnl_pct: 0, consecutive_losses: 0 })
      expect(store.circuitBreakerStatus?.halted).toBe(false)
    })
  })

  describe('updateAuthMode', () => {
    it('sets authorization mode', () => {
      const store = usePortfolioStore()
      expect(store.authMode).toBe('suggestion')

      store.updateAuthMode('semi_auto')
      expect(store.authMode).toBe('semi_auto')

      store.updateAuthMode('full_auto')
      expect(store.authMode).toBe('full_auto')
    })
  })

  describe('fetchAll', () => {
    it('falls back to mock data in dev mode and sets loaded status', async () => {
      const store = usePortfolioStore()
      await store.fetchAll()
      // In test (DEV=true), mock fallback populates data
      expect(store.status).toBe('loaded')
      expect(store.accounts.length).toBeGreaterThan(0)
      expect(store.positions.length).toBeGreaterThan(0)
    })
  })

  describe('fetchCircuitBreakerStatus', () => {
    it('falls back to safe defaults in dev mode', async () => {
      const store = usePortfolioStore()
      await store.fetchCircuitBreakerStatus()
      expect(store.circuitBreakerStatus).toEqual({
        halted: false,
        daily_pnl_pct: 0,
        consecutive_losses: 0,
      })
    })
  })
})
