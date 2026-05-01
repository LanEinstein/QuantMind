/** SSE composable for streaming agent debate events.
 *
 * Consumes the discriminated union defined in types/agent:
 *   { event_type: 'agent_started'      | ... }
 *   { event_type: 'agent_completed'    | ... }
 *   { event_type: 'pipeline_completed' | ... }
 *   { event_type: 'error'              | ... }
 *
 * The caller (views/AgentDebate) normally creates a job via
 * analysisApi.createJob() and passes the returned job_id to connect().
 * SSE is strictly GET, so POST happens on the API client side first.
 */

import { ref, readonly, onUnmounted } from 'vue'
import type {
  SSEEvent,
  AgentCompletedEvent,
  AgentRole,
} from '@/types/agent'
import { analysisApi } from '@/api/analysis'

export interface AgentSSEState {
  readonly events: readonly AgentCompletedEvent[]
  readonly connected: boolean
  readonly error: string | null
  readonly errorRecordId: string | null
  readonly thinkingAgent: AgentRole | null
  readonly pipelineRecordId: string | null
  readonly pipelineSignalId: string | null
  readonly completed: boolean
}

/** Map technical SSE error to a Chinese user-facing message.
 *
 * Backend error events may carry raw exception strings ("Analysis
 * failed: ...") that can leak stack trace fragments; strip the prefix
 * and fall back to a generic Chinese phrase when the message is empty
 * or clearly an internal diagnostic.
 */
function humanizeErrorMessage(raw: string | undefined): string {
  if (!raw) return '分析发生错误'
  const stripped = raw.replace(/^Analysis failed:\s*/i, '').trim()
  if (!stripped) return '分析发生错误'
  // Keep Chinese messages verbatim; wrap any leaked English so the user
  // still sees actionable text.
  if (/[一-鿿]/.test(stripped)) return stripped
  return `分析发生错误：${stripped}`
}

export function useAgentSSE() {
  const events = ref<AgentCompletedEvent[]>([])
  const connected = ref(false)
  const error = ref<string | null>(null)
  const errorRecordId = ref<string | null>(null)
  const thinkingAgent = ref<AgentRole | null>(null)
  const pipelineRecordId = ref<string | null>(null)
  const pipelineSignalId = ref<string | null>(null)
  const completed = ref(false)

  let eventSource: EventSource | null = null

  function connect(jobId: string) {
    disconnect()
    resetState()

    const url = analysisApi.streamUrl(jobId)

    try {
      eventSource = new EventSource(url)
    } catch (e) {
      console.warn('SSE connect failed', e)
      error.value = '无法建立实时连接'
      return
    }

    eventSource.onopen = () => {
      connected.value = true
      error.value = null
    }

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as SSEEvent
        handleEvent(data)
      } catch {
        console.warn('SSE: invalid message format')
      }
    }

    eventSource.onerror = () => {
      connected.value = false
      if (!completed.value) {
        error.value = '实时连接已中断'
      }
      eventSource?.close()
      eventSource = null
    }
  }

  function handleEvent(event: SSEEvent) {
    switch (event.event_type) {
      case 'agent_started':
        thinkingAgent.value = event.agent
        return
      case 'agent_completed':
        thinkingAgent.value = null
        events.value = [...events.value, event]
        return
      case 'pipeline_completed':
        completed.value = true
        pipelineRecordId.value = event.record_id
        pipelineSignalId.value = event.signal_id
        thinkingAgent.value = null
        disconnect()
        return
      case 'error':
        error.value = humanizeErrorMessage(event.message)
        errorRecordId.value = event.record_id ?? null
        thinkingAgent.value = null
        disconnect()
        return
    }
  }

  function disconnect() {
    eventSource?.close()
    eventSource = null
    connected.value = false
    thinkingAgent.value = null
  }

  function resetState() {
    events.value = []
    error.value = null
    errorRecordId.value = null
    pipelineRecordId.value = null
    pipelineSignalId.value = null
    completed.value = false
  }

  function reset() {
    disconnect()
    resetState()
  }

  onUnmounted(disconnect)

  return {
    events: readonly(events),
    connected: readonly(connected),
    error: readonly(error),
    errorRecordId: readonly(errorRecordId),
    thinkingAgent: readonly(thinkingAgent),
    pipelineRecordId: readonly(pipelineRecordId),
    pipelineSignalId: readonly(pipelineSignalId),
    completed: readonly(completed),
    connect,
    disconnect,
    reset,
  }
}
