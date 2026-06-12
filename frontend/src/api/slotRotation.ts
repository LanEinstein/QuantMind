/** Z-004 — read-only ≤5-slot rotation API client. */

import { apiGet } from './request'
import type { SlotRotationPayload } from '@/types/slotRotation'

export const slotRotationApi = {
  get(): Promise<SlotRotationPayload> {
    return apiGet<SlotRotationPayload>('/api/slot-rotation')
  },
}
