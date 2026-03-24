/** Market data API client. */

import { apiGet } from './request'
import type { IndexQuote, SectorQuote, CapitalFlowData } from '@/types/market'

export const marketApi = {
  getIndices: () => apiGet<IndexQuote[]>('/api/market/indices'),
  getSectors: () => apiGet<SectorQuote[]>('/api/market/sectors'),
  getCapitalFlow: () => apiGet<CapitalFlowData>('/api/market/capital-flow'),
}
