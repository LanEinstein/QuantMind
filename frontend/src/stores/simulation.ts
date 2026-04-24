/** Pinia store for MiroFish simulation state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  SimulationResult,
  SimulationHistoryItem,
  SimulationComparison,
  SimulationStatus,
  SentimentSnapshot,
  HiddenVariable,
  InflectionPoint,
  ExtremeScenario,
  MomentumShift,
  SimulationConfig,
  EnrichedInflectionViewModel,
} from '@/types/simulation'
import { simulationApi } from '@/api/simulation'
import {
  splitScenariosByDirection,
  enrichInflection,
} from '@/stores/transformers/simulation'

export const useSimulationStore = defineStore('simulation', () => {
  // --- State ---
  const currentSimulation = ref<SimulationResult | null>(null)
  const history = ref<SimulationHistoryItem[]>([])
  const comparison = ref<SimulationComparison | null>(null)
  const status = ref<SimulationStatus>('idle')
  const searchQuery = ref('')

  const isDev = import.meta.env.DEV

  // --- Computed ---
  const sentimentData = computed((): readonly SentimentSnapshot[] => {
    return currentSimulation.value?.sentiment_evolution ?? []
  })

  const hiddenVariables = computed((): readonly HiddenVariable[] => {
    return currentSimulation.value?.hidden_variables ?? []
  })

  const inflectionPoints = computed((): readonly InflectionPoint[] => {
    return currentSimulation.value?.key_inflection_points ?? []
  })

  const extremeScenarios = computed((): readonly ExtremeScenario[] => {
    return currentSimulation.value?.extreme_scenarios ?? []
  })

  const eventTitle = computed((): string => {
    const sim = currentSimulation.value
    if (!sim) return ''
    return sim.event?.title ?? sim.event_summary
  })

  const importanceScore = computed((): number => {
    return currentSimulation.value?.event?.importance_score ?? 0
  })

  const simulationConfig = computed((): SimulationConfig | null => {
    return currentSimulation.value?.simulation_config ?? null
  })

  const filteredHistory = computed(() => {
    if (!searchQuery.value) return history.value
    const q = searchQuery.value.toLowerCase()
    return history.value.filter((item) =>
      item.event_title.toLowerCase().includes(q),
    )
  })

  const momentumShifts = computed((): readonly MomentumShift[] => {
    return currentSimulation.value?.momentum_shifts ?? []
  })

  const enrichedInflections = computed((): readonly EnrichedInflectionViewModel[] => {
    const points = currentSimulation.value?.key_inflection_points ?? []
    const sentiment = sentimentData.value
    return points.map((ip) => enrichInflection(ip, sentiment))
  })

  const upsideScenarios = computed((): readonly ExtremeScenario[] => {
    const { upside } = splitScenariosByDirection(extremeScenarios.value)
    return upside
  })

  const downsideScenarios = computed((): readonly ExtremeScenario[] => {
    const { downside } = splitScenariosByDirection(extremeScenarios.value)
    return downside
  })

  const totalRounds = computed((): number => {
    return sentimentData.value.length
  })

  // --- Actions ---
  async function fetchLatest() {
    status.value = 'loading'
    try {
      currentSimulation.value = await simulationApi.getLatest()
      status.value = 'loaded'
    } catch {
      console.warn('Failed to fetch latest simulation, using mock data')
      if (isDev) {
        currentSimulation.value = mockSimulationResult()
        status.value = 'loaded'
      } else {
        status.value = 'error'
      }
    }
  }

  async function fetchById(id: string) {
    status.value = 'loading'
    try {
      currentSimulation.value = await simulationApi.getById(id)
      status.value = 'loaded'
    } catch {
      console.warn('Failed to fetch simulation by id, using mock data')
      if (isDev) {
        currentSimulation.value = mockSimulationResultForId(id)
        status.value = 'loaded'
      } else {
        currentSimulation.value = null
        status.value = 'error'
      }
    }
  }

  async function fetchHistory() {
    try {
      history.value = await simulationApi.getHistory({
        search: searchQuery.value || undefined,
        limit: 50,
      })
    } catch {
      console.warn('Failed to fetch simulation history, using mock data')
      if (isDev) history.value = mockHistory()
    }
  }

  async function fetchComparison(aId: string, bId: string) {
    try {
      comparison.value = await simulationApi.compare(aId, bId)
    } catch {
      console.warn('Failed to fetch simulation comparison')
      comparison.value = null
    }
  }

  function clearComparison() {
    comparison.value = null
  }

  return {
    currentSimulation,
    history,
    comparison,
    status,
    searchQuery,
    sentimentData,
    hiddenVariables,
    inflectionPoints,
    extremeScenarios,
    eventTitle,
    importanceScore,
    simulationConfig,
    filteredHistory,
    momentumShifts,
    enrichedInflections,
    upsideScenarios,
    downsideScenarios,
    totalRounds,
    fetchLatest,
    fetchById,
    fetchHistory,
    fetchComparison,
    clearComparison,
  }
})

// --- Mock data (deterministic, no Math.random) ---

function generateSentimentEvolution(): SentimentSnapshot[] {
  const rounds: SentimentSnapshot[] = []
  const narratives = [
    '政策预期升温', '流动性宽松预期', '外资流入窗口', '情绪冲顶',
    '获利回吐压力', '震荡整理', '资金面验证', '中期逻辑回归',
    '外资数据落地', '情绪平稳', '中性观望', '政策效果显现',
    '债市协同走强', '股市分化', '结构性机会', '情绪修复',
    '资金持续流入', '预期充分定价', '短线盘整', '趋势确认',
  ]
  for (let r = 1; r <= 20; r++) {
    // Deterministic curve: bullish peaks at R8, dips at R14
    const t = r / 20
    const bullish = 0.45 + 0.20 * Math.sin(t * Math.PI * 1.5) - 0.05 * t
    const bearish = 0.30 - 0.12 * Math.sin(t * Math.PI * 1.5) + 0.04 * t
    const neutral = 1.0 - bullish - bearish
    const intensity = 0.4 + 0.5 * Math.sin((r / 20) * Math.PI)
    rounds.push({
      round: r,
      bullish: Math.round(bullish * 100) / 100,
      bearish: Math.round(bearish * 100) / 100,
      neutral: Math.round(neutral * 100) / 100,
      dominant_narrative: narratives[r - 1],
      intensity: Math.round(intensity * 100) / 100,
    })
  }
  return rounds
}

function mockSimulationResult(): SimulationResult {
  return {
    id: 'sim-mock-001',
    event: {
      title: '央行宣布定向降准50个基点',
      content:
        '中国人民银行宣布对符合条件的金融机构定向降低存款准备金率50个基点，释放长期资金约1.2万亿元，旨在加大对实体经济的支持力度。',
      importance_score: 9,
      sectors: ['银行', '房地产', '基建'],
      stocks: ['601318', '600036', '000001'],
    },
    event_summary:
      '央行宣布定向降准50个基点，释放长期资金约1.2万亿元，利好银行和房地产板块',
    simulation_config: {
      agent_count: 300,
      rounds: 20,
      model: 'kimi-k2.6',
    },
    sentiment_evolution: generateSentimentEvolution(),
    hidden_variables: [
      {
        variable: '外资加速流入概率',
        probability: 0.72,
        reasoning:
          '降准信号叠加人民币汇率企稳，北向资金配置窗口打开。历史数据显示降准后20个交易日北向资金净流入概率达68%。仿真中约216个Agent（72%）在第10轮后转为积极配置外资相关标的。',
        agent_consensus_ratio: 0.72,
        is_absent_from_original: false,
      },
      {
        variable: '房地产板块过度反应概率',
        probability: 0.45,
        reasoning:
          '市场可能过度解读为地产利好，但降准资金主要流向制造业和小微企业。仿真中机构类Agent普遍认为地产受益有限，但散户类Agent情绪亢奋。',
        agent_consensus_ratio: 0.41,
        is_absent_from_original: true,
      },
      {
        variable: '游资抢筹创业板概率',
        probability: 0.58,
        reasoning:
          '流动性宽松环境下，游资偏好高弹性小盘股。仿真中游资类Agent在第5轮后集中转向创业板标的，形成短期动量效应。',
        agent_consensus_ratio: 0.55,
        is_absent_from_original: true,
      },
      {
        variable: '央行后续降息概率',
        probability: 0.33,
        reasoning:
          '降准后市场预期进一步宽松，但当前通胀压力和汇率约束限制降息空间。分析师类Agent对此分歧明显。',
        agent_consensus_ratio: 0.29,
        is_absent_from_original: true,
      },
      {
        variable: '债市收益率下行概率',
        probability: 0.81,
        reasoning:
          '降准直接增加银行间流动性，短端利率下行确定性高。仿真中固收类Agent一致性最强，243个Agent（81%）预期10年期国债收益率下降5-10bp。',
        agent_consensus_ratio: 0.81,
        is_absent_from_original: false,
      },
      {
        variable: '中小银行补涨概率',
        probability: 0.39,
        reasoning:
          '降准对中小银行净息差改善更显著，但市场关注度集中在大行。仿真中仅117个Agent关注中小银行标的。',
        agent_consensus_ratio: 0.36,
        is_absent_from_original: true,
      },
    ],
    key_inflection_points: [
      {
        day: 3,
        event: '情绪高点，获利回吐压力出现，短线资金开始撤离',
        inflection_type: 'exhaustion',
        before_sentiment: { bullish: 0.62, bearish: 0.18, neutral: 0.20 },
        after_sentiment: { bullish: 0.48, bearish: 0.30, neutral: 0.22 },
        confidence: 0.78,
      },
      {
        day: 8,
        event: '央行公开市场操作释放额外信号，市场重新定价宽松预期',
        inflection_type: 'narrative_convergence',
        before_sentiment: { bullish: 0.45, bearish: 0.32, neutral: 0.23 },
        after_sentiment: { bullish: 0.58, bearish: 0.24, neutral: 0.18 },
        confidence: 0.65,
      },
      {
        day: 14,
        event: '真实资金面数据落地，修正过度乐观预期，情绪回落',
        inflection_type: 'sentiment_reversal',
        before_sentiment: { bullish: 0.55, bearish: 0.25, neutral: 0.20 },
        after_sentiment: { bullish: 0.38, bearish: 0.42, neutral: 0.20 },
        confidence: 0.88,
      },
      {
        day: 18,
        event: '外资数据公布，北向资金净流入验证降准利好逻辑',
        inflection_type: 'cascade_trigger',
        before_sentiment: { bullish: 0.42, bearish: 0.38, neutral: 0.20 },
        after_sentiment: { bullish: 0.56, bearish: 0.28, neutral: 0.16 },
        confidence: 0.72,
      },
    ],
    extreme_scenarios: [
      {
        scenario: '超预期利好叠加',
        probability: 0.15,
        impact: '沪指上涨3-5%，银行板块领涨',
        direction: 'upside',
        trigger_conditions: '美联储同步降息，外资大幅加仓',
        early_warning_signals: '北向资金单日净流入超200亿\n期货基差走强\n银行股放量突破',
      },
      {
        scenario: '利好出尽见光死',
        probability: 0.25,
        impact: '冲高回落，沪指下跌1-2%',
        direction: 'downside',
        trigger_conditions: '量能不足，获利盘集中出逃',
        early_warning_signals: '缩量上涨信号\n融资余额快速下降\n外资转为净卖出',
      },
      {
        scenario: '外部冲击对冲',
        probability: 0.08,
        impact: '美联储鹰派言论抵消降准利好，市场震荡',
        direction: 'downside',
        trigger_conditions: '美联储意外鹰派表态，人民币贬值压力上升',
        early_warning_signals: '离岸人民币快速贬值\n美债收益率急升\n黄金大涨避险',
      },
      {
        scenario: '政策超预期宽松',
        probability: 0.10,
        impact: '后续降息预期升温，成长股全面走强',
        direction: 'upside',
        trigger_conditions: '经济数据低于预期，政策工具箱进一步打开',
        early_warning_signals: 'PMI连续低于50\n房地产成交量持续萎缩\nCPI环比转负',
      },
    ],
    momentum_shifts: [
      {
        round_number: 3,
        direction: 'bullish_to_bearish' as const,
        magnitude: 0.18,
        trigger_narrative: '情绪过热后的自然回落',
      },
      {
        round_number: 8,
        direction: 'bearish_to_bullish' as const,
        magnitude: 0.22,
        trigger_narrative: '央行额外操作重燃信心',
      },
      {
        round_number: 14,
        direction: 'bullish_to_bearish' as const,
        magnitude: 0.31,
        trigger_narrative: '资金面数据修正乐观预期',
      },
    ],
    recommended_action:
      '短期看多，建议分批建仓银行和基建板块。警惕第3日获利回吐压力，可在回调时加仓。中期关注第14日资金面数据验证。',
    cost_rmb: 3.42,
    duration_seconds: 238.5,
    created_at: '2026-03-24T09:30:00Z',
  }
}

/** Return a mock result with the ID matching the history item, or the default mock. */
function mockSimulationResultForId(id: string): SimulationResult {
  const base = mockSimulationResult()
  const historyItem = mockHistory().find((h) => h.id === id)
  if (!historyItem || id === 'sim-mock-001') return base

  const allEvolution = generateSentimentEvolution()
  const trimmedEvolution = allEvolution.slice(0, historyItem.rounds)

  return {
    ...base,
    id,
    event: {
      ...base.event,
      title: historyItem.event_title,
      importance_score: historyItem.importance_score,
    },
    event_summary: historyItem.event_title,
    simulation_config: {
      ...base.simulation_config,
      agent_count: historyItem.agent_count,
      rounds: historyItem.rounds,
    },
    sentiment_evolution: trimmedEvolution,
    recommended_action: historyItem.recommended_action,
    cost_rmb: historyItem.cost_rmb,
    duration_seconds: historyItem.duration_seconds,
    created_at: historyItem.created_at,
  }
}

function mockHistory(): SimulationHistoryItem[] {
  return [
    {
      id: 'sim-mock-001',
      event_title: '央行宣布定向降准50个基点',
      importance_score: 9,
      agent_count: 300,
      rounds: 20,
      recommended_action: '短期看多，建议分批建仓银行和基建板块',
      cost_rmb: 3.42,
      duration_seconds: 238.5,
      created_at: '2026-03-24T09:30:00Z',
    },
    {
      id: 'sim-mock-002',
      event_title: '美联储暗示年内可能降息',
      importance_score: 8,
      agent_count: 300,
      rounds: 20,
      recommended_action: '利好外资流入，关注北向资金动向',
      cost_rmb: 3.18,
      duration_seconds: 215.2,
      created_at: '2026-03-22T14:00:00Z',
    },
    {
      id: 'sim-mock-003',
      event_title: '新能源汽车补贴政策延续',
      importance_score: 7,
      agent_count: 200,
      rounds: 15,
      recommended_action: '新能源产业链短期利好，关注电池和整车龙头',
      cost_rmb: 2.05,
      duration_seconds: 156.8,
      created_at: '2026-03-20T10:15:00Z',
    },
    {
      id: 'sim-mock-004',
      event_title: '房地产调控政策微调',
      importance_score: 8,
      agent_count: 300,
      rounds: 20,
      recommended_action: '地产板块短期反弹，但中期仍需观望政策持续性',
      cost_rmb: 3.35,
      duration_seconds: 242.1,
      created_at: '2026-03-18T09:45:00Z',
    },
    {
      id: 'sim-mock-005',
      event_title: '科技领域出口管制升级',
      importance_score: 9,
      agent_count: 300,
      rounds: 25,
      recommended_action: '国产替代概念短期走强，但需警惕情绪过热',
      cost_rmb: 4.12,
      duration_seconds: 312.6,
      created_at: '2026-03-15T11:30:00Z',
    },
  ]
}
