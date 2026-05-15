/** API client for the P1-5 §1.1 five-freeze-source system-status endpoint. */

import { apiGet } from './request'
import type { FreezeSourcesPayload } from '@/types/systemStatus'

export const systemStatusApi = {
  getFreezeSources(): Promise<FreezeSourcesPayload> {
    return apiGet<FreezeSourcesPayload>('/api/system-status/freeze-sources')
  },
}
