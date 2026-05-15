/**
 * Front-end mirror of the backend InstructionPlan + 3-tab reason
 * drawer payload returned by ``/api/instruction-plans``.
 *
 * The three reason-tab keys (``REASON_NAMESPACES``) are locked in
 * backend/api/instruction_plans.py — front-end drawer keys + redline
 * tests must keep them in sync.
 */

export type InstructionSide = 'BUY' | 'SELL' | 'HOLD'

export type InstructionStatus =
  | 'DRAFT'
  | 'VALIDATED'
  | 'REJECTED'
  | 'DISPATCHED'
  | 'FILLED'
  | 'EXPIRED'
  | 'AMBIGUOUS'

export interface InstructionPlanSummary {
  readonly instruction_id: string
  readonly trade_date: string
  readonly stock_code: string
  readonly stock_name: string
  readonly side: InstructionSide
  readonly status: InstructionStatus
  readonly volume: number | null
  readonly limit_price: number | null
  readonly valid_until: string
  readonly created_at: string
  readonly rejection_reason: string | null
}

export interface InstructionPlanListPayload {
  readonly plans: readonly InstructionPlanSummary[]
  readonly total: number
  readonly repository_status: 'ok' | 'unavailable'
  readonly timestamp?: string
}

export interface BuilderEarlyReturnRow {
  readonly reason_namespace: string
  readonly payload: Record<string, unknown>
  readonly at: string
}

export interface RiskEngineCheckRow {
  readonly check_id: number
  readonly rule_name: string
  /** ``null`` while Phase D legacy 7-check fills indices 7..13 (P0-7 amendment). */
  readonly passed: boolean | null
  readonly threshold: string | null
  readonly actual: string | null
  readonly message: string
}

export interface BrokerAtFillRow {
  readonly outcome: 'FILLED' | 'REJECTED' | 'EXPIRED' | string
  /** ``price_limit_violation_at_fill`` is locked under this tab only. */
  readonly reason: string | null
  readonly fill_price: number | null
  readonly fill_volume: number | null
}

export interface InstructionPlanDetailPayload {
  readonly plan: InstructionPlanSummary
  readonly evidence_ids: readonly string[]
  readonly debate_round_count: number
  readonly invalidation_summary: string
  readonly reason_tabs: {
    readonly builder_early_return: readonly BuilderEarlyReturnRow[]
    readonly risk_engine_check: readonly RiskEngineCheckRow[]
    readonly broker_at_fill: BrokerAtFillRow | null
  }
}

export const REASON_NAMESPACES = [
  'builder_early_return',
  'risk_engine_check',
  'broker_at_fill',
] as const

export type ReasonNamespace = (typeof REASON_NAMESPACES)[number]
