/**
 * Lock the front-end REASON_NAMESPACES tuple to the backend tuple in
 * backend/api/instruction_plans.py so any rename / addition crashes the
 * suite instead of silently breaking the drawer's tab keys.
 */
import { describe, expect, it } from 'vitest'
import { REASON_NAMESPACES } from '@/types/instructionPlan'

describe('REASON_NAMESPACES contract (G-003)', () => {
  it('locks the three-namespace tuple ordering', () => {
    expect(REASON_NAMESPACES).toEqual([
      'builder_early_return',
      'risk_engine_check',
      'broker_at_fill',
    ])
  })

  it('forbids the broker-side namespace from leaking into the engine namespace', () => {
    // namespace lock: ``price_limit_violation_at_fill`` only lives under
    // ``broker_at_fill`` — backend tests guarantee this; front-end
    // sanity test makes the rule explicit.
    expect(REASON_NAMESPACES).not.toContain('price_limit_violation_at_fill')
  })
})
