/** TypeScript interfaces for system settings. */

export interface ProviderInfo {
  readonly name?: string
  readonly base_url: string
  readonly api_key: string
  readonly default_model: string
}

export interface FallbackInfo {
  readonly provider: string
  readonly model: string
}

export interface AgentInfo {
  readonly name: string
  readonly provider: string
  readonly model: string
  readonly fallback: FallbackInfo | null
  readonly frequency: string
  readonly task: string
}

export interface DefaultsInfo {
  readonly temperature: number
  readonly max_tokens: number
}

export interface LLMConfig {
  readonly providers: Record<string, ProviderInfo>
  readonly agents: Record<string, AgentInfo>
  readonly defaults: DefaultsInfo
}

export interface ConnectionTestResult {
  readonly provider: string
  readonly connected: boolean
  readonly latency_ms: number
  readonly error: string | null
}

export interface DataSourceStatus {
  readonly name: string
  readonly type: string
  readonly status: 'connected' | 'configured' | 'error' | 'unknown'
  readonly latency_ms: number
  readonly error: string | null
  readonly role?: string
}

export interface MiroFishSimulation {
  readonly enabled: boolean
  readonly agent_count: number
  readonly rounds: number
  readonly trigger_threshold: number
  readonly model: string
}

export interface MiroFishConfig {
  readonly simulation: MiroFishSimulation
  readonly cost_estimate?: {
    readonly input_price_per_1k: number
    readonly output_price_per_1k: number
    readonly chars_per_token?: number
  }
}

export interface DailyCostEntry {
  readonly date: string
  readonly agent_name: string
  readonly provider: string
  readonly prompt_tokens: number
  readonly completion_tokens: number
  readonly requests: number
  readonly cost_rmb: number
}

export interface CostSummary {
  readonly period: string
  readonly days: number
  readonly entries: readonly DailyCostEntry[]
  readonly total_cost_rmb: number
  readonly total_requests: number
  readonly total_prompt_tokens: number
  readonly total_completion_tokens: number
  readonly by_agent: Record<string, number>
  readonly by_provider: Record<string, number>
  readonly daily_totals: Record<string, number>
}
