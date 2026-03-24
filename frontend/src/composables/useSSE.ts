/** SSE composable for streaming agent debate events. */

import { ref, readonly, onUnmounted } from 'vue'
import type { SSEEvent, SSEStatus } from '@/types/agent'

export interface AgentSSEState {
  readonly events: readonly SSEEvent[]
  readonly connected: boolean
  readonly error: string | null
  readonly thinkingAgent: string | null
}

export function useAgentSSE() {
  const events = ref<SSEEvent[]>([])
  const connected = ref(false)
  const error = ref<string | null>(null)
  const thinkingAgent = ref<string | null>(null)

  let eventSource: EventSource | null = null

  function getBaseUrl(): string {
    return import.meta.env.VITE_API_BASE_URL || ''
  }

  function connect(analysisId: string) {
    disconnect()
    error.value = null

    const url = `${getBaseUrl()}/api/analysis/stream/${encodeURIComponent(analysisId)}`

    try {
      eventSource = new EventSource(url)
    } catch (e) {
      error.value = `Failed to connect SSE: ${e}`
      return
    }

    eventSource.onopen = () => {
      connected.value = true
      error.value = null
    }

    eventSource.onmessage = (event) => {
      try {
        const data: SSEEvent = JSON.parse(event.data)
        handleEvent(data)
      } catch {
        console.warn('SSE: invalid message format')
      }
    }

    eventSource.onerror = () => {
      connected.value = false
      error.value = 'SSE connection lost'
      eventSource?.close()
      eventSource = null
    }
  }

  function handleEvent(event: SSEEvent) {
    if (event.status === 'thinking') {
      thinkingAgent.value = event.agent
      return
    }

    // status === 'done': add the completed event
    thinkingAgent.value = null
    events.value = [...events.value, event]
  }

  function disconnect() {
    eventSource?.close()
    eventSource = null
    connected.value = false
    thinkingAgent.value = null
  }

  function reset() {
    disconnect()
    events.value = []
    error.value = null
  }

  onUnmounted(disconnect)

  return {
    events: readonly(events),
    connected: readonly(connected),
    error: readonly(error),
    thinkingAgent: readonly(thinkingAgent),
    connect,
    disconnect,
    reset,
  }
}
