/** Pinia store for agent debate state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  AnalysisSummary,
  AnalysisDetail,
  DebateRound,
  RiskAssessment,
  FundManagerDecision,
  AnalysisStatus,
} from '@/types/agent'
import { analysisApi } from '@/api/analysis'

const MOCK_ENABLED =
  import.meta.env.VITE_ENABLE_MOCK_AGENT === '1' ||
  import.meta.env.VITE_ENABLE_MOCK_AGENT === 'true'

const STOCK_CODE_PATTERN = /^\d{6}$/

export const useAgentStore = defineStore('agent', () => {
  // --- State ---
  const currentAnalysis = ref<AnalysisDetail | null>(null)
  const history = ref<AnalysisSummary[]>([])
  const loading = ref(false)
  const historyLoading = ref(false)
  const historyError = ref<string | null>(null)
  const analysisStatus = ref<AnalysisStatus>('pending')
  const searchQuery = ref('')
  const searchDate = ref('')
  const lastError = ref<string | null>(null)

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

  const currentRound = computed(
    () => currentAnalysis.value?.current_round ?? 0,
  )
  const maxRounds = computed(
    () => currentAnalysis.value?.max_rounds ?? 2,
  )

  async function fetchDetail(id: string) {
    loading.value = true
    lastError.value = null
    try {
      currentAnalysis.value = await analysisApi.getDetail(id)
      analysisStatus.value = currentAnalysis.value.status
    } catch (err: unknown) {
      lastError.value = getErrorMessage(err)
      console.warn(
        'Failed to fetch analysis detail:',
        lastError.value,
      )
      if (MOCK_ENABLED) {
        // Developer opt-in fallback; disabled in production builds unless
        // VITE_ENABLE_MOCK_AGENT is set. No silent fallback.
        currentAnalysis.value = mockAnalysisDetail(id)
        analysisStatus.value = currentAnalysis.value.status
      } else {
        currentAnalysis.value = null
        analysisStatus.value = 'failed'
      }
    } finally {
      loading.value = false
    }
  }

  async function fetchHistory() {
    historyLoading.value = true
    historyError.value = null
    lastError.value = null
    try {
      // The history search input supports both stock codes and names.
      // The backend /api/analysis/history endpoint only accepts exact
      // stock_code matches, so forward the code filter ONLY when the
      // query looks like a 6-digit A-share code; otherwise let the
      // client-side `filteredHistory` computed property handle fuzzy
      // name/substring search against the full recent list.
      const trimmed = searchQuery.value.trim()
      const codeFilter = STOCK_CODE_PATTERN.test(trimmed) ? trimmed : undefined
      history.value = await analysisApi.getHistory({
        stock_code: codeFilter,
        trade_date: searchDate.value || undefined,
        limit: 50,
      })
    } catch (err: unknown) {
      const msg = getErrorMessage(err)
      historyError.value = '加载分析历史失败，请稍后重试'
      lastError.value = msg
      console.warn('Failed to fetch analysis history:', msg)
      history.value = MOCK_ENABLED ? mockHistory() : []
    } finally {
      historyLoading.value = false
    }
  }

  function resetCurrentAnalysis() {
    currentAnalysis.value = null
    analysisStatus.value = 'pending'
  }

  return {
    currentAnalysis,
    history,
    loading,
    historyLoading,
    historyError,
    analysisStatus,
    searchQuery,
    searchDate,
    lastError,
    filteredHistory,
    debates,
    riskAssessment,
    decision,
    currentRound,
    maxRounds,
    fetchDetail,
    fetchHistory,
    resetCurrentAnalysis,
  }
})

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return 'Unknown error'
}

// ---------------------------------------------------------------------------
// Dev-only mock data — activated via VITE_ENABLE_MOCK_AGENT env flag. Never
// used in production builds. Kept for manual UI validation without a live
// backend.
// ---------------------------------------------------------------------------

function mockAnalysisDetail(id: string): AnalysisDetail {
  return {
    id,
    run_id: `mock-${id}`,
    stock_code: '600519',
    stock_name: '贵州茅台',
    trade_date: '2026-03-24',
    status: 'completed',
    max_rounds: 2,
    current_round: 2,
    steps: [],
    analysts: [],
    intelligence_officer: null,
    debates: [
      {
        round: 1,
        bull: {
          role: 'bull',
          round: 1,
          content: '（mock）看多观点',
          evidence: [],
          model: 'Kimi',
          timestamp: '2026-03-24T10:15:00Z',
        },
        bear: {
          role: 'bear',
          round: 1,
          content: '（mock）看空观点',
          evidence: [],
          model: 'Kimi',
          timestamp: '2026-03-24T10:16:30Z',
        },
      },
    ],
    risk_assessment: {
      model: 'Kimi',
      checks: [],
      position_limit: '15%',
      raw_text: '（mock）风控评估',
    },
    decision: {
      model: 'Kimi',
      score: 72,
      score_label: '偏多',
      action: '买入',
      target_price: 2150,
      stop_loss: null,
      position_pct: null,
      reasoning: '（mock）综合决策',
      confidence: 0.72,
      risk_score: 0.35,
    },
    signal_id: null,
    created_at: '2026-03-24T10:10:00Z',
    completed_at: '2026-03-24T10:22:00Z',
    error: null,
  }
}

function mockHistory(): AnalysisSummary[] {
  return [
    {
      id: 'a001',
      run_id: 'mock-run-001',
      stock_code: '600519',
      stock_name: '贵州茅台',
      trade_date: '2026-03-24',
      status: 'completed',
      action: '买入',
      confidence: 0.72,
      risk_score: 0.35,
      signal_id: null,
      created_at: '2026-03-24T10:10:00Z',
      completed_at: '2026-03-24T10:22:00Z',
    },
  ]
}
