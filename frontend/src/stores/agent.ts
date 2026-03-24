/** Pinia store for agent debate state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  AnalysisSummary,
  AnalysisDetail,
  DebateRound,
  RiskAssessment,
  FundManagerDecision,
  SSEEvent,
  AnalysisStatus,
  AuthMode,
} from '@/types/agent'
import { analysisApi } from '@/api/analysis'

export const useAgentStore = defineStore('agent', () => {
  // --- State ---
  const currentAnalysis = ref<AnalysisDetail | null>(null)
  const history = ref<AnalysisSummary[]>([])
  const loading = ref(false)
  const analysisStatus = ref<AnalysisStatus>('pending')
  const authMode = ref<AuthMode>('suggest')
  const searchQuery = ref('')
  const searchDate = ref('')

  const isDev = import.meta.env.DEV

  // --- Computed ---
  const filteredHistory = computed(() => {
    let items = history.value
    if (searchQuery.value) {
      const q = searchQuery.value.toLowerCase()
      items = items.filter(
        (a) =>
          a.stock_code.includes(q) ||
          a.stock_name.toLowerCase().includes(q),
      )
    }
    if (searchDate.value) {
      items = items.filter((a) => a.trade_date === searchDate.value)
    }
    return items
  })

  const debates = computed((): readonly DebateRound[] => {
    return currentAnalysis.value?.debates ?? []
  })

  const riskAssessment = computed((): RiskAssessment | null => {
    return currentAnalysis.value?.risk_assessment ?? null
  })

  const decision = computed((): FundManagerDecision | null => {
    return currentAnalysis.value?.decision ?? null
  })

  const currentRound = computed(() => currentAnalysis.value?.current_round ?? 0)
  const maxRounds = computed(() => currentAnalysis.value?.max_rounds ?? 4)

  // --- Actions ---
  async function triggerAnalysis(stockCode: string, maxDebateRounds = 2) {
    loading.value = true
    analysisStatus.value = 'running'
    try {
      const result = await analysisApi.trigger({
        stock_code: stockCode,
        max_debate_rounds: maxDebateRounds,
      })
      return result.id
    } catch {
      analysisStatus.value = 'failed'
      console.warn('Failed to trigger analysis')
      return null
    } finally {
      loading.value = false
    }
  }

  async function fetchDetail(id: string) {
    loading.value = true
    try {
      currentAnalysis.value = await analysisApi.getDetail(id)
      analysisStatus.value = currentAnalysis.value.status
    } catch {
      // Backend GET /api/analysis/{id} is not yet implemented.
      // Fall back to mock data so the page is usable during development.
      console.warn('Failed to fetch analysis detail, using mock data')
      currentAnalysis.value = mockAnalysisDetail(id)
      analysisStatus.value = currentAnalysis.value.status
    } finally {
      loading.value = false
    }
  }

  async function fetchHistory() {
    try {
      history.value = await analysisApi.getHistory({
        stock_code: searchQuery.value || undefined,
        date: searchDate.value || undefined,
        limit: 50,
      })
    } catch {
      // Backend GET /api/analysis/history is not yet implemented.
      console.warn('Failed to fetch analysis history, using mock data')
      history.value = mockHistory()
    }
  }

  function applySSEEvent(event: SSEEvent) {
    if (!currentAnalysis.value) return

    // Only bull/bear researcher events produce debate round arguments
    if (event.agent !== 'bull_researcher' && event.agent !== 'bear_researcher') return

    const analysis = currentAnalysis.value
    const role: 'bull' | 'bear' = event.agent === 'bull_researcher' ? 'bull' : 'bear'

    const existingRounds = [...analysis.debates]
    const roundIdx = existingRounds.findIndex((r) => r.round === event.round)

    const argument = {
      role: role as 'bull' | 'bear',
      round: event.round,
      content: event.content,
      evidence: event.evidence ?? [],
      model: 'MiniMax' as const,
      timestamp: event.timestamp,
    }

    if (roundIdx >= 0) {
      const existing = existingRounds[roundIdx]
      existingRounds[roundIdx] = {
        ...existing,
        [role]: argument,
      }
    } else {
      existingRounds.push({
        round: event.round,
        bull: role === 'bull' ? argument : null,
        bear: role === 'bear' ? argument : null,
      })
    }

    currentAnalysis.value = {
      ...analysis,
      debates: existingRounds,
      current_round: event.round,
    }
  }

  function setAuthMode(mode: AuthMode) {
    authMode.value = mode
  }

  return {
    currentAnalysis,
    history,
    loading,
    analysisStatus,
    authMode,
    searchQuery,
    searchDate,
    filteredHistory,
    debates,
    riskAssessment,
    decision,
    currentRound,
    maxRounds,
    triggerAnalysis,
    fetchDetail,
    fetchHistory,
    applySSEEvent,
    setAuthMode,
  }
})

// --- Mock data for dev mode ---

function mockAnalysisDetail(id: string): AnalysisDetail {
  return {
    id,
    stock_code: '600519',
    stock_name: '贵州茅台',
    trade_date: '2026-03-24',
    status: 'completed',
    max_rounds: 4,
    current_round: 3,
    debates: [
      {
        round: 1,
        bull: {
          role: 'bull',
          round: 1,
          content:
            '茅台2025年全年营收同比增长15.2%，净利润增速达18%，批价坚挺在2800元以上。一季度开门红数据优异，经销商打款积极。消费升级趋势下，高端白酒的品牌护城河持续加深。',
          evidence: [
            { label: '基本面', model: 'Qwen', status: 'positive', detail: '营收增长15.2%，超市场预期。净利润率维持50%以上高位。' },
            { label: '情绪', model: 'DeepSeek', status: 'positive', detail: '社交媒体讨论度高，机构研报一致看好。市场情绪偏乐观。' },
            { label: '仿真', model: 'MiroFish', status: 'positive', detail: '300 Agent仿真显示78%看多共识，短期上涨概率较高。' },
          ],
          model: 'MiniMax',
          timestamp: '2026-03-24T10:15:00Z',
        },
        bear: {
          role: 'bear',
          round: 1,
          content:
            '当前PE 32倍已处于历史高位区间，外资近3个月持续减持，持仓占比下降3个百分点。宏观经济复苏不及预期，消费降级风险犹存。白酒行业库存周期见顶，批价稳定或是控量保价的结果。',
          evidence: [
            { label: '技术面', model: 'Qwen', status: 'mixed', detail: 'MACD顶背离，RSI进入超买区域。但均线系统仍多头排列。' },
            { label: '情报', model: 'MiniMax', status: 'mixed', detail: '行业库存周期见顶信号明显，经销商反馈动销放缓。' },
            { label: '资金面', model: 'DeepSeek', status: 'negative', detail: '北向资金连续5日净卖出茅台，累计减持12亿元。' },
          ],
          model: 'MiniMax',
          timestamp: '2026-03-24T10:16:30Z',
        },
      },
      {
        round: 2,
        bull: {
          role: 'bull',
          round: 2,
          content:
            '北向资金减持属短期调仓行为，从历史来看不改变长期趋势。茅台的定价权和品牌壁垒使其在经济下行期反而是避险资产。PE估值需考虑净利润增速，PEG仅1.8倍，仍处合理区间。',
          evidence: [
            { label: '基本面', model: 'Qwen', status: 'positive', detail: 'PEG 1.8倍，考虑增速后估值合理。自由现金流充裕。' },
            { label: '情绪', model: 'DeepSeek', status: 'positive', detail: '机构持仓集中度上升，保险资金加仓明显。' },
          ],
          model: 'MiniMax',
          timestamp: '2026-03-24T10:18:00Z',
        },
        bear: {
          role: 'bear',
          round: 2,
          content:
            '即便PEG合理，绝对估值偏高限制了上行空间。反腐政策持续深化对高端白酒的政务消费构成压力。茅台增速已见顶，未来3年复合增速预计降至10%以内。',
          evidence: [
            { label: '技术面', model: 'Qwen', status: 'negative', detail: '周线级别出现放量滞涨，上方套牢盘压力较大。' },
            { label: '情报', model: 'MiniMax', status: 'mixed', detail: '政务消费比例已降低，但替代效应使商务消费增长放缓。' },
          ],
          model: 'MiniMax',
          timestamp: '2026-03-24T10:19:30Z',
        },
      },
      {
        round: 3,
        bull: {
          role: 'bull',
          round: 3,
          content:
            '总结: 茅台核心竞争力未变，短期波动不影响长期投资价值。国际化布局打开增长空间，直营渠道占比提升有利于利润率持续改善。建议逢回调分批建仓。',
          evidence: [
            { label: '基本面', model: 'Qwen', status: 'positive', detail: '直营占比提升至40%，渠道利润率改善趋势确认。' },
            { label: '仿真', model: 'MiroFish', status: 'positive', detail: '中长期仿真（60轮）显示价值回归概率高。' },
          ],
          model: 'MiniMax',
          timestamp: '2026-03-24T10:21:00Z',
        },
        bear: null,
      },
    ],
    risk_assessment: {
      model: 'MiniMax',
      checks: [
        { label: '仓位合规', passed: true },
        { label: '止损设置', passed: true },
        { label: '集中度合规', passed: true },
        { label: '流动性检查', passed: true },
        { label: '波动率风控', passed: false },
      ],
      position_limit: '15%',
      raw_text:
        '该标的流动性充足，日均成交额超30亿。建议单只仓位不超过15%。当前波动率偏高，需设置严格止损位。',
    },
    decision: {
      model: 'MiniMax',
      score: 72,
      score_label: '偏多',
      action: '买入',
      target_price: 2150,
      stop_loss: 1850,
      position_pct: 5,
      reasoning:
        '综合多空辩论和风控评估，茅台基本面稳健，估值合理偏高但增长确定性强。短期技术面承压，建议小仓位试探性建仓，目标价2150元，止损1850元。',
      confidence: 0.72,
      risk_score: 0.35,
    },
    created_at: '2026-03-24T10:10:00Z',
  }
}

function mockHistory(): AnalysisSummary[] {
  return [
    {
      id: 'a001',
      stock_code: '600519',
      stock_name: '贵州茅台',
      trade_date: '2026-03-24',
      status: 'completed',
      action: '买入',
      score: 72,
      created_at: '2026-03-24T10:10:00Z',
    },
    {
      id: 'a002',
      stock_code: '000858',
      stock_name: '五粮液',
      trade_date: '2026-03-23',
      status: 'completed',
      action: '持有',
      score: 55,
      created_at: '2026-03-23T14:30:00Z',
    },
    {
      id: 'a003',
      stock_code: '300750',
      stock_name: '宁德时代',
      trade_date: '2026-03-23',
      status: 'completed',
      action: '卖出',
      score: 35,
      created_at: '2026-03-23T09:45:00Z',
    },
    {
      id: 'a004',
      stock_code: '601318',
      stock_name: '中国平安',
      trade_date: '2026-03-22',
      status: 'completed',
      action: '买入',
      score: 68,
      created_at: '2026-03-22T11:00:00Z',
    },
    {
      id: 'a005',
      stock_code: '000001',
      stock_name: '平安银行',
      trade_date: '2026-03-22',
      status: 'failed',
      action: '持有',
      score: 50,
      created_at: '2026-03-22T09:30:00Z',
    },
  ]
}
