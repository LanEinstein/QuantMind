/** Pure formatters for the account-lines panel (kept out of the view for tests). */

import type { LedgerRow } from '@/api/accountLines'

const CNY = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function formatMoney(value: number): string {
  return `${CNY.format(value)} 元`
}

export function formatSigned(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : ''
  const body = new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
  return `${sign}${body}`
}

const KIND_LABEL: Record<LedgerRow['kind'], string> = {
  fill: '成交',
  cash: '资金',
  adjust: '修正',
}

const KIND_PILL: Record<LedgerRow['kind'], string> = {
  fill: 'pill-success',
  cash: 'pill-info',
  adjust: 'pill-warning',
}

export function kindLabel(kind: LedgerRow['kind']): string {
  return KIND_LABEL[kind] ?? kind
}

export function kindPill(kind: LedgerRow['kind']): string {
  return KIND_PILL[kind] ?? 'pill-info'
}

/** One human line per ledger row, by kind (fields are kind-specific). */
export function describeLedgerRow(row: LedgerRow): string {
  if (row.kind === 'fill') {
    const side = row.side === 'BUY' ? '买入' : '卖出'
    const fees = (row.commission ?? 0) + (row.stamp_tax ?? 0) + (row.transfer_fee ?? 0)
    return `${side} ${row.code} ${row.volume} 股 @ ${(row.price ?? 0).toFixed(2)},净额 ${formatMoney(row.net ?? 0)}(费用 ${formatMoney(fees)})`
  }
  if (row.kind === 'cash') {
    const amount = row.amount ?? 0
    return `${amount >= 0 ? '入金' : '出金'} ${formatMoney(Math.abs(amount))}`
  }
  const delta = row.volume_delta ?? 0
  return `${row.code} 持仓修正 ${formatSigned(delta, 0)} 股(此刻生效)`
}
