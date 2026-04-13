/** WebSocket composable with auto-reconnect and exponential backoff. */

import { ref, onUnmounted } from 'vue'
import type { WsMessage, IndexQuote, NewsArticle } from '@/types/market'
import type { PositionItem, CircuitBreakerStatus } from '@/types/trading'
import { useMarketStore } from '@/stores/market'
import { usePortfolioStore } from '@/stores/portfolio'

const MAX_RECONNECT_DELAY = 30_000
const BASE_DELAY = 1_000

export function useWebSocket() {
  const connected = ref(false)
  const reconnectAttempt = ref(0)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function getWsUrl(): string {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return import.meta.env.VITE_WS_URL || `${proto}//${location.host}/ws/market`
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    try {
      ws = new WebSocket(getWsUrl())
    } catch {
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      reconnectAttempt.value = 0
      console.log('WebSocket connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg: WsMessage = JSON.parse(event.data)
        handleMessage(msg)
      } catch {
        console.warn('WebSocket: invalid message')
      }
    }

    ws.onclose = () => {
      connected.value = false
      scheduleReconnect()
    }

    ws.onerror = () => {
      connected.value = false
      ws?.close()
    }
  }

  function handleMessage(msg: WsMessage) {
    const marketStore = useMarketStore()

    switch (msg.type) {
      case 'index_update':
        marketStore.updateIndex(msg.data as IndexQuote)
        break
      case 'news':
        marketStore.pushNews(msg.data as NewsArticle)
        break
      case 'signal':
        marketStore.latestSignal = msg.data as string
        break
      case 'status':
        Object.assign(marketStore.systemStatus, msg.data)
        break

      // Portfolio channel messages
      case 'position_update': {
        const payload = msg.data as { account_id: string; positions: PositionItem[] }
        const portfolio = usePortfolioStore()
        if (payload.account_id === portfolio.activeAccountId) {
          portfolio.updatePositionsFromWs(payload.positions)
        }
        break
      }
      case 'circuit_breaker_update': {
        const portfolio = usePortfolioStore()
        portfolio.updateCircuitBreaker(msg.data as CircuitBreakerStatus)
        break
      }
      case 'auth_mode_change': {
        const payload = msg.data as { mode: string }
        const portfolio = usePortfolioStore()
        portfolio.updateAuthMode(payload.mode)
        break
      }
      case 'approval_update': {
        const portfolio = usePortfolioStore()
        portfolio.fetchPendingApprovals()
        break
      }
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    const delay = Math.min(BASE_DELAY * 2 ** reconnectAttempt.value, MAX_RECONNECT_DELAY)
    reconnectAttempt.value++
    console.log(`WebSocket reconnecting in ${delay}ms (attempt ${reconnectAttempt.value})`)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    ws?.close()
    ws = null
    connected.value = false
  }

  onUnmounted(disconnect)

  return { connected, connect, disconnect }
}
