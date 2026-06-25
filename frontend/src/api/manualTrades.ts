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
 * `EXTERNAL_TRADE_ID_PATTERN` (SEQ is locked to exactly 3 digits, so a UUID
 * slice is not format-legal here).
 *
 * F7 (production-hardening 2026-06-25): the 3-digit seq is now the timestamp's
 * **milliseconds** (sub-second precision), not `Math.random()*1000`. Random
 * 1000-buckets could collide for two trades on the same code+side+second (and a
 * collision is silently deduped away by the backend idempotency key). Millis is
 * (a) deterministic for the same `at` — so retries of one submit keep reusing
 * the same id (idempotency) — and (b) distinct across any two submits that fall
 * in different milliseconds of the same second, which random could not promise.
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
  const seq = String(at.getMilliseconds()).padStart(3, '0')
  return `UT-${y}${mo}${d}-${h}${mi}${s}-${code}-${side}-${seq}`
}
