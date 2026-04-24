/** Pinia store for market data state management. */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type {
  IndexQuote,
  SectorQuote,
  CapitalFlowData,
  NewsArticle,
  MarketStats,
  SystemStatus,
} from '@/types/market'
import { marketApi } from '@/api/market'
import { newsApi } from '@/api/news'

export const useMarketStore = defineStore('market', () => {
  // --- State ---
  const indices = ref<IndexQuote[]>([])
  const sectors = ref<SectorQuote[]>([])
  const capitalFlow = ref<CapitalFlowData | null>(null)
  const news = ref<NewsArticle[]>([])
  const marketStats = ref<MarketStats>({
    rising: 0, falling: 0, flat: 0, limit_up: 0, limit_down: 0,
  })
  const systemStatus = ref<SystemStatus>({
    deepseek: false, qwen: false, kimi: false,
    adata: false, akshare: false,
    daily_cost_rmb: 0,
    risk_status: 'normal',
    auth_mode: 'suggest',
  })
  const latestSignal = ref<string>('')
  const loading = ref(false)

  const isDev = import.meta.env.DEV

  // --- Actions ---
  async function fetchIndices() {
    try {
      indices.value = await marketApi.getIndices()
    } catch {
      console.warn('Failed to fetch indices')
      if (isDev) indices.value = mockIndices()
    }
  }

  async function fetchSectors() {
    try {
      sectors.value = await marketApi.getSectors()
    } catch {
      console.warn('Failed to fetch sectors')
      if (isDev) sectors.value = mockSectors()
    }
  }

  async function fetchCapitalFlow() {
    try {
      capitalFlow.value = await marketApi.getCapitalFlow()
    } catch {
      console.warn('Failed to fetch capital flow')
      if (isDev) capitalFlow.value = mockCapitalFlow()
    }
  }

  async function fetchNews() {
    try {
      news.value = await newsApi.getLatest(30)
    } catch {
      console.warn('Failed to fetch news')
      if (isDev) news.value = mockNews()
    }
  }

  async function fetchAll() {
    loading.value = true
    await Promise.allSettled([
      fetchIndices(),
      fetchSectors(),
      fetchCapitalFlow(),
      fetchNews(),
    ])
    // Populate marketStats from mock data in dev (no backend endpoint yet)
    if (isDev && marketStats.value.rising === 0) {
      marketStats.value = {
        rising: 2156, falling: 1823, flat: 421,
        limit_up: 38, limit_down: 12,
      }
    }
    loading.value = false
  }

  // --- WS update handlers ---
  function updateIndex(data: IndexQuote) {
    const idx = indices.value.findIndex((i) => i.code === data.code)
    if (idx >= 0) {
      indices.value = indices.value.map((item, i) => (i === idx ? data : item))
    }
  }

  function pushNews(article: NewsArticle) {
    news.value = [article, ...news.value.slice(0, 49)]
  }

  return {
    indices, sectors, capitalFlow, news, marketStats,
    systemStatus, latestSignal, loading,
    fetchAll, fetchIndices, fetchSectors, fetchCapitalFlow, fetchNews,
    updateIndex, pushNews,
  }
})

// --- Mock data generators (for development when backend is offline) ---

function mockIndices(): IndexQuote[] {
  return [
    { code: '000001', name: '上证指数', price: 3150.42, change_pct: 0.85, volume: 3.2e10, amount: 4.1e11, timestamp: new Date().toISOString() },
    { code: '399001', name: '深证成指', price: 10280.15, change_pct: -0.32, volume: 4.5e10, amount: 5.2e11, timestamp: new Date().toISOString() },
    { code: '399006', name: '创业板指', price: 2085.67, change_pct: 1.25, volume: 1.8e10, amount: 2.3e11, timestamp: new Date().toISOString() },
  ]
}

function mockSectors(): SectorQuote[] {
  const names = ['银行', '房地产', '医药生物', '电子', '食品饮料', '有色金属', '化工', '汽车', '计算机', '电力设备', '机械设备', '传媒']
  return names.map((name) => ({
    name,
    change_pct: +(Math.random() * 6 - 3).toFixed(2),
    leader_code: '600000',
    leader_name: `${name}龙头`,
    leader_change_pct: +(Math.random() * 10 - 2).toFixed(2),
    timestamp: new Date().toISOString(),
  }))
}

function mockCapitalFlow(): CapitalFlowData {
  return {
    north_net_inflow: 3.2e9,
    main_net_inflow: -1.5e9,
    timestamp: new Date().toISOString(),
  }
}

function mockNews(): NewsArticle[] {
  const items = [
    { title: '央行宣布定向降准50个基点', importance_score: 9, source: '新华社' },
    { title: '美联储暗示年内降息路径不变', importance_score: 8, source: '路透社' },
    { title: '新能源汽车销量创月度新高', importance_score: 7, source: '中国证券报' },
    { title: '外资连续3日净买入超百亿', importance_score: 6, source: '上海证券报' },
    { title: '多地出台房地产支持新政', importance_score: 5, source: '经济日报' },
    { title: '沪深两市成交额突破万亿', importance_score: 4, source: '证券时报' },
    { title: '北交所新股申购提示', importance_score: 2, source: '北交所公告' },
  ]
  return items.map((item) => ({
    ...item,
    content: `${item.title}的详细报道内容...`,
    url: '#',
    publish_time: new Date().toISOString(),
    stock_codes: [],
    has_simulation: item.importance_score >= 7,
    simulation_summary: item.importance_score >= 7 ? '仿真显示短期看多情绪占优' : undefined,
  }))
}
