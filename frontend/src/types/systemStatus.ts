/**
 * Front-end mirror of backend P1-5 §1.1 five-freeze-source schema.
 *
 * Source names + envelope shape are locked in
 * backend/api/system_status.py:FREEZE_SOURCE_NAMES — any drift here
 * fails the menu-spec contract tests + redline-check.
 */

export type FreezeSourceName =
  | 'mode_switch'
  | 'reconciliation_ticket'
  | 'circuit_breaker'
  | 'data_quality'
  | 'eod_pipeline'

export type FreezeProbeStatus = 'ok' | 'unavailable'

export interface FreezeSourceBase {
  readonly name: FreezeSourceName
  readonly active: boolean
  readonly status: FreezeProbeStatus
  readonly reason: string | null
}

export interface ModeSwitchFreezeSource extends FreezeSourceBase {
  readonly name: 'mode_switch'
  readonly context: {
    readonly reason: string | null
    readonly started_at: string | null
    readonly initiated_by: string | null
    readonly from_mode: string | null
    readonly to_mode: string | null
  } | null
}

export interface ReconciliationTicketFreezeSource extends FreezeSourceBase {
  readonly name: 'reconciliation_ticket'
  readonly ticket_id: string | null
}

export interface CircuitBreakerFreezeSource extends FreezeSourceBase {
  readonly name: 'circuit_breaker'
  readonly halted_at: string | null
  readonly consecutive_losses: number | null
}

export interface DataQualityFreezeSource extends FreezeSourceBase {
  readonly name: 'data_quality'
  readonly code: string | null
}

export interface EodPipelineFreezeSource extends FreezeSourceBase {
  readonly name: 'eod_pipeline'
  readonly raised_at: string | null
  readonly trade_date: string | null
}

export type FreezeSource =
  | ModeSwitchFreezeSource
  | ReconciliationTicketFreezeSource
  | CircuitBreakerFreezeSource
  | DataQualityFreezeSource
  | EodPipelineFreezeSource

export interface FreezeSourcesPayload {
  readonly sources: readonly FreezeSource[]
  readonly any_active: boolean
  readonly any_unavailable: boolean
  readonly timestamp: string
}

export const FREEZE_SOURCE_LABELS: Record<FreezeSourceName, string> = {
  mode_switch: '模式切换',
  reconciliation_ticket: '对账冻结',
  circuit_breaker: '熔断冷却',
  data_quality: '数据质量',
  eod_pipeline: '日终管线',
}

export const FREEZE_SOURCE_NAMES: readonly FreezeSourceName[] = [
  'mode_switch',
  'reconciliation_ticket',
  'circuit_breaker',
  'data_quality',
  'eod_pipeline',
]
