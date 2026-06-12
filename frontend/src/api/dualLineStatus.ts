/** Z-005 — read-only dual-line run-state API client. */

import { apiGet } from './request'
import type { DualLineStatusPayload } from '@/types/dualLineStatus'

export const dualLineStatusApi = {
  get(): Promise<DualLineStatusPayload> {
    return apiGet<DualLineStatusPayload>('/api/dual-line-status')
  },
}
