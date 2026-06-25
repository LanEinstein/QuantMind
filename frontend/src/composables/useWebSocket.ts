/** WebSocket composable with auto-reconnect and exponential backoff.
 *
 * G-009 — the dispatcher now covers the locked 14-kind ``WsMessageType``
 * union. Six legacy kinds (index/signal/news/status/position/breaker)
 * keep their existing store hookups; the eight new system-channel kinds
 * are fanned out as readonly composable state so views that opt-in via
 * the returned refs (instruction plans, equity points, etc.) get the
 * latest payload without each one having to wire its own `ref`.
 */

import { ref, onUnmounted } from 'vue'
import type {
  WsMessage,
  WsMessageType,
  IndexQuote,
  NewsArticle,
} from '@/types/market'
import { WS_MESSAGE_TYPES } from '@/types/market'
import type { PositionItem, CircuitBreakerStatus } from '@/types/trading'
import { useMarketStore } from '@/stores/market'
import { usePortfolioStore } from '@/stores/portfolio'

const MAX_RECONNECT_DELAY = 30_000
const BASE_DELAY = 1_000
// F1 (production-hardening 2026-06-25): a days-open dashboard must not reconnect-
// storm against a flapping endpoint, and a permanently-down socket must surface
// instead of silently showing stale data.
const STABILITY_WINDOW_MS = 10_000 // only clear the backoff after a stable open
const MAX_RECONNECT_ATTEMPTS = 10 // after this, give up + surface a banner
const JITTER_RATIO = 0.3 // ±30% so many clients don't thunder together

export function useWebSocket() {
  const connected = ref(false)
  const reconnectAttempt = ref(0)
  // F1: true once auto-reconnect has exhausted MAX_RECONNECT_ATTEMPTS — views
  // bind this to a persistent "实时连接断开" banner. Cleared on a fresh connect()
  // or a successful (re)open.
  const connectionLost = ref(false)

  // G-009 — last-payload refs for the 8 new system-channel kinds.
  // Views can subscribe to whichever ref they need without each one
  // wiring its own onmessage handler.
  const lastInstructionPlanUpdate = ref<unknown | null>(null)
  const lastBrokerEvent = ref<unknown | null>(null)
  const lastEquityPointUpdate = ref<unknown | null>(null)
  const lastDataQualityBreach = ref<unknown | null>(null)
  const lastFreezeSourceUpdate = ref<unknown | null>(null)
  const lastTicketUpdate = ref<unknown | null>(null)
  const lastAcceptanceReportReady = ref<unknown | null>(null)
  const lastFeishuMessageReceived = ref<unknown | null>(null)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  // F1: fires STABILITY_WINDOW_MS after an open; only then is the connection
  // judged "stable" and the backoff counter reset. A flap that closes before it
  // fires keeps the escalating delay.
  let stableTimer: ReturnType<typeof setTimeout> | null = null

  function getWsUrl(): string {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return import.meta.env.VITE_WS_URL || `${proto}//${location.host}/ws/market`
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    // F1 (codex): connectionLost is cleared ONLY on a real onopen — NOT here.
    // Backoff-driven retries re-enter connect() repeatedly while the socket is
    // still dead; clearing the flag here would flicker the "断开" banner off on
    // every silent retry. A manual retry uses reconnectNow() (resets backoff).
    try {
      ws = new WebSocket(getWsUrl())
    } catch {
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      connectionLost.value = false
      console.log('WebSocket connected')
      // F1: do NOT reset the backoff immediately — a flapping endpoint that
      // OPENs then drops within milliseconds would otherwise snap back to a 1s
      // retry and storm. Only clear the attempt counter once the socket has
      // stayed open for the stability window.
      if (stableTimer) clearTimeout(stableTimer)
      stableTimer = setTimeout(() => {
        reconnectAttempt.value = 0
        stableTimer = null
      }, STABILITY_WINDOW_MS)
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
      // F1: a close before the stability window means the connection was NOT
      // stable — keep the escalating backoff rather than resetting.
      if (stableTimer) {
        clearTimeout(stableTimer)
        stableTimer = null
      }
      scheduleReconnect()
    }

    ws.onerror = () => {
      connected.value = false
      ws?.close()
    }
  }

  function handleMessage(msg: WsMessage) {
    const marketStore = useMarketStore()

    // Defense in depth — the backend allowlist already filters this,
    // but accepting only the 14 locked kinds here keeps the type
    // narrowing honest and rejects any forbidden type that slipped
    // through (P1-5 §2 红线 4).
    if (!(WS_MESSAGE_TYPES as readonly string[]).includes(msg.type)) {
      return
    }

    switch (msg.type as WsMessageType) {
      // === 6 retained =================================================
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
      case 'position_update': {
        const payload = msg.data as {
          account_id: string
          positions: PositionItem[]
        }
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

      // === 8 new (G-009) =============================================
      case 'instruction_plan_update':
        lastInstructionPlanUpdate.value = msg.data
        break
      case 'broker_event':
        lastBrokerEvent.value = msg.data
        break
      case 'equity_point_update':
        lastEquityPointUpdate.value = msg.data
        break
      case 'data_quality_breach':
        lastDataQualityBreach.value = msg.data
        break
      case 'freeze_source_update':
        lastFreezeSourceUpdate.value = msg.data
        break
      case 'ticket_update':
        lastTicketUpdate.value = msg.data
        break
      case 'acceptance_report_ready':
        lastAcceptanceReportReady.value = msg.data
        break
      case 'feishu_message_received':
        lastFeishuMessageReceived.value = msg.data
        break
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    // F1 (codex): once we've failed MAX_RECONNECT_ATTEMPTS times, SURFACE the
    // persistent "实时连接断开" banner (connectionLost) — but KEEP retrying at the
    // capped delay so the feed self-heals when the backend returns. Permanently
    // giving up (the prior return) left a days-open dashboard showing stale data
    // forever until a manual page reload.
    if (reconnectAttempt.value >= MAX_RECONNECT_ATTEMPTS) {
      connectionLost.value = true
    }
    const backoff = Math.min(BASE_DELAY * 2 ** reconnectAttempt.value, MAX_RECONNECT_DELAY)
    // F1: full ±JITTER_RATIO band so N clients reconnecting after one backend
    // blip don't thunder together; floored at BASE_DELAY.
    const jitter = backoff * JITTER_RATIO * (Math.random() * 2 - 1)
    const delay = Math.max(BASE_DELAY, Math.round(backoff + jitter))
    reconnectAttempt.value++
    console.log(`WebSocket reconnecting in ${delay}ms (attempt ${reconnectAttempt.value})`)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  /**
   * F1 (codex): manual retry — reset the backoff so a user-triggered "重连"
   * recovers immediately instead of waiting out the capped delay. Bound to the
   * persistent-disconnect banner's action button.
   */
  function reconnectNow() {
    reconnectAttempt.value = 0
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    connect()
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (stableTimer) {
      clearTimeout(stableTimer)
      stableTimer = null
    }
    ws?.close()
    ws = null
    connected.value = false
  }

  onUnmounted(disconnect)

  return {
    connected,
    connectionLost,
    reconnectAttempt,
    connect,
    reconnectNow,
    disconnect,
    // G-009 — readonly handles for the 8 new system-channel kinds.
    lastInstructionPlanUpdate,
    lastBrokerEvent,
    lastEquityPointUpdate,
    lastDataQualityBreach,
    lastFreezeSourceUpdate,
    lastTicketUpdate,
    lastAcceptanceReportReady,
    lastFeishuMessageReceived,
  }
}
