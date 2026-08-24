import { describe, expect, it } from 'vitest'
import { describeLedgerRow, formatMoney, formatSigned } from '@/utils/accountLines'

describe('accountLines formatters', () => {
  it('formats CNY with two decimals and grouping', () => {
    expect(formatMoney(150000)).toBe('150,000.00 元')
    expect(formatMoney(-12.5)).toBe('-12.50 元')
  })

  it('formats signed numbers', () => {
    expect(formatSigned(2.5)).toBe('+2.50')
    expect(formatSigned(-1.234, 4)).toBe('-1.2340')
    expect(formatSigned(0)).toBe('0.00')
    expect(formatSigned(1500)).toBe('+1,500.00')
  })

  it('describes a fill with net and fee total', () => {
    const text = describeLedgerRow({
      kind: 'fill', recorded_at: '', code: '002271', side: 'BUY', volume: 5000,
      price: 12.3, commission: 9.23, stamp_tax: 0, transfer_fee: 0.62, net: 61509.85,
    })
    expect(text).toBe('买入 002271 5000 股 @ 12.30,净额 61,509.85 元(费用 9.85 元)')
  })

  it('describes cash in/out and adjust rows', () => {
    expect(describeLedgerRow({ kind: 'cash', recorded_at: '', amount: 150000 })).toBe('入金 150,000.00 元')
    expect(describeLedgerRow({ kind: 'cash', recorded_at: '', amount: -500 })).toBe('出金 500.00 元')
    expect(describeLedgerRow({ kind: 'adjust', recorded_at: '', code: '002271', volume_delta: -100 }))
      .toBe('002271 持仓修正 -100 股(此刻生效)')
  })
})
