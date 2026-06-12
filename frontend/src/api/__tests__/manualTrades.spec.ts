import { describe, it, expect } from 'vitest'
import { mintExternalTradeId } from '@/api/manualTrades'

// Mirrors backend EXTERNAL_TRADE_ID_PATTERN
// (backend/models/manual_trade.py): UT-YYYYMMDD-HHMMSS-CODE6-(BUY|SELL)-SEQ3.
const PATTERN = /^UT-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$/

describe('mintExternalTradeId (AD-005)', () => {
  it('mints an id matching the backend UT- pattern', () => {
    const id = mintExternalTradeId('600519', 'SELL', new Date(2026, 5, 12, 14, 5, 9))
    expect(id).toMatch(PATTERN)
    expect(id).toContain('-600519-SELL-')
    expect(id.startsWith('UT-20260612-140509-')).toBe(true)
  })

  it('embeds the BUY side and code', () => {
    const id = mintExternalTradeId('000001', 'BUY', new Date(2026, 0, 2, 9, 30, 0))
    expect(id).toMatch(PATTERN)
    expect(id).toContain('-000001-BUY-')
  })
})
