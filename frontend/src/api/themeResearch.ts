/** Z-002 — read-only industry-chain reverse-deduction API client. */

import { apiGet } from './request'
import type { IndustryChainPayload } from '@/types/themeResearch'

export const themeResearchApi = {
  getIndustryChain(): Promise<IndustryChainPayload> {
    return apiGet<IndustryChainPayload>('/api/theme-research/industry-chain')
  },
}
