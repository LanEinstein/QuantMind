/** Pinia store for system settings state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  LLMConfig,
  ConnectionTestResult,
  DataSourceStatus,
  MiroFishConfig,
  CostSummary,
} from '@/types/settings'
import { settingsApi } from '@/api/settings'

export const useSettingsStore = defineStore('settings', () => {
  // --- State ---
  const llmConfig = ref<LLMConfig | null>(null)
  const dataSources = ref<DataSourceStatus[]>([])
  const mirofishConfig = ref<MiroFishConfig | null>(null)
  const costSummary = ref<CostSummary | null>(null)
  const connectionTests = ref<Record<string, ConnectionTestResult>>({})
  const loading = ref(false)
  const costPeriod = ref<'daily' | 'weekly'>('daily')
  const costDays = ref(30)

  const isDev = import.meta.env.DEV

  // --- Computed ---
  const providerList = computed(() => {
    if (!llmConfig.value) return []
    return Object.entries(llmConfig.value.providers).map(([key, info]) => ({
      key,
      ...info,
    }))
  })

  const agentList = computed(() => {
    if (!llmConfig.value) return []
    return Object.entries(llmConfig.value.agents).map(([key, info]) => ({
      key,
      ...info,
    }))
  })

  const dailyCostTotals = computed(() => {
    return costSummary.value?.daily_totals ?? {}
  })

  const costByProvider = computed(() => {
    return costSummary.value?.by_provider ?? {}
  })

  const costByAgent = computed(() => {
    return costSummary.value?.by_agent ?? {}
  })

  const monthlyProjection = computed(() => {
    if (!costSummary.value || costSummary.value.days === 0) return 0
    const dailyAvg = costSummary.value.total_cost_rmb / costSummary.value.days
    return Math.round(dailyAvg * 30 * 100) / 100
  })

  // --- Actions ---
  async function fetchLLMConfig() {
    try {
      llmConfig.value = await settingsApi.getLLMConfig()
    } catch {
      console.warn('Failed to fetch LLM config')
      if (isDev) llmConfig.value = mockLLMConfig()
    }
  }

  async function updateLLMConfig(data: Record<string, unknown>) {
    loading.value = true
    try {
      llmConfig.value = await settingsApi.updateLLMConfig(data)
    } finally {
      loading.value = false
    }
  }

  async function testProvider(provider: string): Promise<ConnectionTestResult> {
    try {
      const result = await settingsApi.testLLMProvider(provider)
      connectionTests.value = { ...connectionTests.value, [provider]: result }
      return result
    } catch {
      const fallback: ConnectionTestResult = {
        provider,
        connected: false,
        latency_ms: 0,
        error: 'Request failed',
      }
      connectionTests.value = { ...connectionTests.value, [provider]: fallback }
      return fallback
    }
  }

  async function fetchDataSources() {
    try {
      dataSources.value = await settingsApi.getDataSources()
    } catch {
      console.warn('Failed to fetch data sources')
      if (isDev) dataSources.value = mockDataSources()
    }
  }

  async function testDataSource(source: string): Promise<DataSourceStatus> {
    const result = await settingsApi.testDataSource(source)
    dataSources.value = dataSources.value.map((ds) =>
      ds.name.toLowerCase() === source.toLowerCase() ? result : ds,
    )
    return result
  }

  async function fetchMiroFishConfig() {
    try {
      mirofishConfig.value = await settingsApi.getMiroFishConfig()
    } catch {
      console.warn('Failed to fetch MiroFish config')
      if (isDev) mirofishConfig.value = mockMiroFishConfig()
    }
  }

  async function updateMiroFishConfig(data: Record<string, unknown>) {
    loading.value = true
    try {
      mirofishConfig.value = await settingsApi.updateMiroFishConfig(data)
    } finally {
      loading.value = false
    }
  }

  async function fetchCostStats() {
    try {
      costSummary.value = await settingsApi.getCostStats({
        period: costPeriod.value,
        days: costDays.value,
      })
    } catch {
      console.warn('Failed to fetch cost stats')
      if (isDev) costSummary.value = mockCostSummary()
    }
  }

  return {
    llmConfig, dataSources, mirofishConfig, costSummary,
    connectionTests, loading, costPeriod, costDays,
    providerList, agentList, dailyCostTotals, costByProvider,
    costByAgent, monthlyProjection,
    fetchLLMConfig, updateLLMConfig, testProvider,
    fetchDataSources, testDataSource,
    fetchMiroFishConfig, updateMiroFishConfig,
    fetchCostStats,
  }
})

// --- Mock data generators (development when backend offline) ---

function mockLLMConfig(): LLMConfig {
  return {
    providers: {
      deepseek: { base_url: 'https://api.deepseek.com/v1', api_key: '***masked***', default_model: 'deepseek-v4-pro' },
      qwen: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', api_key: '***masked***', default_model: 'qwen3.6-plus' },
      kimi: { base_url: 'https://api.moonshot.ai/v1', api_key: '***masked***', default_model: 'kimi-k2.6' },
    },
    defaults: { temperature: 0.3, max_tokens: 4096 },
    agents: {
      news_crawler: { name: '新闻爬取员', provider: 'deepseek', model: 'deepseek-v4-pro', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'every_5min', task: '财经新闻摘要、分类、重要性评分(0-10)' },
      sentiment_analyst: { name: '情绪分析师', provider: 'deepseek', model: 'deepseek-v4-pro', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'every_30min', task: '社交媒体情绪、论坛情感、恐慌贪婪指数' },
      data_cleaner: { name: '数据清洗员', provider: 'deepseek', model: 'deepseek-v4-pro', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'realtime', task: '原始数据标准化、异常值标记、格式转换' },
      fundamental_analyst: { name: '基本面分析师', provider: 'qwen', model: 'qwen3.6-plus', fallback: { provider: 'deepseek', model: 'deepseek-v4-pro' }, frequency: 'daily_or_event', task: '财报解读、PE/PB估值、行业对比' },
      technical_analyst: { name: '技术分析师', provider: 'qwen', model: 'qwen3.6-plus', fallback: { provider: 'deepseek', model: 'deepseek-v4-pro' }, frequency: 'daily', task: 'K线形态、MACD/RSI/布林带、趋势判断' },
      intelligence_officer: { name: '情报研判员（含MiroFish）', provider: 'kimi', model: 'kimi-k2.6', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'event_triggered', task: '信息融合、隐性变量推演、驱动MiroFish仿真' },
      bull_researcher: { name: '看多研究员', provider: 'kimi', model: 'kimi-k2.6', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'per_trading_day', task: '构建看多论点、寻找上涨催化剂' },
      bear_researcher: { name: '看空研究员', provider: 'kimi', model: 'kimi-k2.6', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'per_trading_day', task: '构建看空论点、寻找下跌风险' },
      risk_officer: { name: '风控官', provider: 'kimi', model: 'kimi-k2.6', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'per_trading_day', task: '投组风险评估、仓位建议、否决权' },
      fund_manager: { name: '基金经理（终局决策）', provider: 'kimi', model: 'kimi-k2.6', fallback: { provider: 'qwen', model: 'qwen3.6-plus' }, frequency: 'per_trading_day', task: '综合所有Agent报告，输出最终买卖信号' },
    },
  }
}

function mockDataSources(): DataSourceStatus[] {
  return [
    { name: 'adata', type: 'market_data', status: 'connected', latency_ms: 45, error: null, role: 'primary' },
    { name: 'AKShare', type: 'market_data', status: 'connected', latency_ms: 120, error: null, role: 'fallback' },
    { name: 'BaoStock', type: 'history_data', status: 'connected', latency_ms: 89, error: null, role: 'fallback' },
    { name: '新闻爬虫', type: 'news', status: 'connected', latency_ms: 200, error: null },
    { name: 'MongoDB', type: 'database', status: 'connected', latency_ms: 5, error: null },
    { name: 'Redis', type: 'cache', status: 'connected', latency_ms: 1, error: null },
  ]
}

function mockMiroFishConfig(): MiroFishConfig {
  return {
    simulation: {
      enabled: true,
      agent_count: 300,
      rounds: 20,
      trigger_threshold: 7,
      model: 'kimi-k2.6',
    },
    cost_estimate: {
      input_price_per_1k: 0.0021,
      output_price_per_1k: 0.0084,
      chars_per_token: 1.5,
    },
  }
}

function mockCostSummary(): CostSummary {
  const entries: Array<CostSummary['entries'][number]> = []
  const dailyTotals: Record<string, number> = {}
  const agents = ['news_crawler', 'sentiment_analyst', 'fundamental_analyst', 'technical_analyst', 'intelligence_officer', 'bull_researcher', 'bear_researcher', 'risk_officer', 'fund_manager']
  const providerMap: Record<string, string> = {
    news_crawler: 'deepseek', sentiment_analyst: 'deepseek',
    fundamental_analyst: 'qwen', technical_analyst: 'qwen',
    intelligence_officer: 'kimi', bull_researcher: 'kimi',
    bear_researcher: 'kimi', risk_officer: 'kimi', fund_manager: 'kimi',
  }

  for (let d = 0; d < 30; d++) {
    const date = new Date()
    date.setDate(date.getDate() - d)
    const dateStr = date.toISOString().slice(0, 10)
    let dayTotal = 0

    for (const agent of agents) {
      const provider = providerMap[agent]
      const cost = +(Math.random() * 0.5 + 0.1).toFixed(4)
      dayTotal += cost
      entries.push({
        date: dateStr, agent_name: agent, provider,
        prompt_tokens: Math.floor(Math.random() * 50000),
        completion_tokens: Math.floor(Math.random() * 20000),
        requests: Math.floor(Math.random() * 50) + 5,
        cost_rmb: cost,
      })
    }
    dailyTotals[dateStr] = +dayTotal.toFixed(4)
  }

  const byAgent: Record<string, number> = {}
  const byProvider: Record<string, number> = {}
  for (const e of entries) {
    byAgent[e.agent_name] = (byAgent[e.agent_name] ?? 0) + e.cost_rmb
    byProvider[e.provider] = (byProvider[e.provider] ?? 0) + e.cost_rmb
  }

  return {
    period: 'daily', days: 30, entries,
    total_cost_rmb: +entries.reduce((s, e) => s + e.cost_rmb, 0).toFixed(4),
    total_requests: entries.reduce((s, e) => s + e.requests, 0),
    total_prompt_tokens: entries.reduce((s, e) => s + e.prompt_tokens, 0),
    total_completion_tokens: entries.reduce((s, e) => s + e.completion_tokens, 0),
    by_agent: byAgent, by_provider: byProvider, daily_totals: dailyTotals,
  }
}
