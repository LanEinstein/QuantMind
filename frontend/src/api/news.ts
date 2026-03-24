/** News API client. */

import { apiGet } from './request'
import type { NewsArticle } from '@/types/market'

export const newsApi = {
  getLatest: (limit = 50) =>
    apiGet<NewsArticle[]>('/api/news/latest', { limit }),
}
