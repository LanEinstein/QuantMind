/**
 * G-009 — WebSocket 14-kind contract tests (frontend half).
 *
 * Pair file: tests/test_ws_g009_contract.py.
 */

import { describe, expect, it } from 'vitest'

import {
  FORBIDDEN_WS_MESSAGE_TYPES,
  WS_MESSAGE_TYPES,
  type WsMessageType,
} from '@/types/market'

describe('WS_MESSAGE_TYPES (G-009 14-kind union)', () => {
  it('locks exactly 14 entries', () => {
    expect(WS_MESSAGE_TYPES.length).toBe(14)
    expect(new Set(WS_MESSAGE_TYPES).size).toBe(14)
  })

  it('keeps the 6 legacy retained kinds', () => {
    for (const kept of [
      'index_update',
      'signal',
      'news',
      'status',
      'position_update',
      'circuit_breaker_update',
    ]) {
      expect((WS_MESSAGE_TYPES as readonly string[]).includes(kept)).toBe(true)
    }
  })

  it('exposes the 8 G-009 system-channel kinds', () => {
    for (const added of [
      'instruction_plan_update',
      'broker_event',
      'equity_point_update',
      'data_quality_breach',
      'freeze_source_update',
      'ticket_update',
      'acceptance_report_ready',
      'feishu_message_received',
    ]) {
      expect((WS_MESSAGE_TYPES as readonly string[]).includes(added)).toBe(true)
    }
  })

  it('excludes the 2 forbidden kinds removed by G-009', () => {
    for (const dropped of FORBIDDEN_WS_MESSAGE_TYPES) {
      expect((WS_MESSAGE_TYPES as readonly string[]).includes(dropped)).toBe(false)
    }
  })

  it('FORBIDDEN_WS_MESSAGE_TYPES locks the 2-element list (P1-5 §2 红线 4)', () => {
    expect([...FORBIDDEN_WS_MESSAGE_TYPES].sort()).toEqual([
      'approval_update',
      'auth_mode_change',
    ])
  })

  it('legacy stock_update is no longer in the WS union (P1-5 §1.1 收窄)', () => {
    // stock_update used to share the index_update slot; G-009 collapses
    // it back into index_update + position_update so the WS surface
    // matches the locked 6+8 list. Surface a clear regression signal.
    expect((WS_MESSAGE_TYPES as readonly string[]).includes('stock_update')).toBe(false)
  })

  it('every WsMessageType narrows to a string literal', () => {
    // Compile-time check via runtime invariant: every entry must be a
    // primitive string (no array / object / undefined slipped in).
    for (const kind of WS_MESSAGE_TYPES) {
      const x: WsMessageType = kind
      expect(typeof x).toBe('string')
      expect(x.length).toBeGreaterThan(0)
    }
  })
})
