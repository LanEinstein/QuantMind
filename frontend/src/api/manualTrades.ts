/**
 * AD-005 — manual-trade write API client (3rd write endpoint).
 *
 * `POST /api/manual-trades` is the ONLY new write endpoint; it is accepted
 * only in feishu_interactive mode (pure-sim → 403). The caller mints the
 * `external_trade_id` once per form so a resubmit dedupes idempotently.
 */

import { apiPost } from './request'
import type {
  ManualTradeRequest,
  ManualTradeResponse,
  ManualTradeSide,
} from '@/types/manualTrade'

export const manualTradesApi = {
  submit(payload: ManualTradeRequest): Promise<ManualTradeResponse> {
    return apiPost<ManualTradeResponse>('/api/manual-trades', payload)
  },
}

/**
 * Mint a `UT-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ` id mirroring the backend
 * `EXTERNAL_TRADE_ID_PATTERN`. The 3-digit seq is a small random suffix so
 * two trades on the same code+second don't collide; the same value is reused
 * across retries of one form submit (idempotency key).
 */
export function mintExternalTradeId(
  code: string,
  side: ManualTradeSide,
  at: Date,
): string {
  const y = at.getFullYear()
  const mo = String(at.getMonth() + 1).padStart(2, '0')
  const d = String(at.getDate()).padStart(2, '0')
  const h = String(at.getHours()).padStart(2, '0')
  const mi = String(at.getMinutes()).padStart(2, '0')
  const s = String(at.getSeconds()).padStart(2, '0')
  const seq = String(Math.floor(Math.random() * 1000)).padStart(3, '0')
  return `UT-${y}${mo}${d}-${h}${mi}${s}-${code}-${side}-${seq}`
}
