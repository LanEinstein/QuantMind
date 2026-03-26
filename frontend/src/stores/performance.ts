/** Pinia store for performance analytics state management. */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  PerformanceData,
  EquityPoint,
  CoreMetrics,
  DrawdownPoint,
  ModelMetric,
  TimeRange,
  BenchmarkType,
  PerformanceStatus,
} from '@/types/performance'
import { performanceApi } from '@/api/performance'

export const usePerformanceStore = defineStore('performance', () => {
  // --- State ---
  const data = ref<PerformanceData | null>(null)
  const status = ref<PerformanceStatus>('idle')
  const timeRange = ref<TimeRange>('month')
  const customDateRange = ref<[string, string] | null>(null)
  const benchmark = ref<BenchmarkType>('hs300')
  const accountId = ref('default')

  const isDev = import.meta.env.DEV

  // --- Computed ---
  const equityCurve = computed((): readonly EquityPoint[] => {
    return data.value?.equity_curve ?? []
  })

  const metrics = computed((): CoreMetrics | null => {
    return data.value?.metrics ?? null
  })

  const drawdownCurve = computed((): readonly DrawdownPoint[] => {
    return data.value?.drawdown_curve ?? []
  })

  const modelContributions = computed((): readonly ModelMetric[] => {
    return data.value?.model_contributions ?? []
  })

  // --- Actions ---
  function getDateRange(): { start: string; end: string } {
    if (timeRange.value === 'custom' && customDateRange.value) {
      return { start: customDateRange.value[0], end: customDateRange.value[1] }
    }
    const now = new Date()
    const end = now.toISOString().slice(0, 10)
    const startDate = new Date(now)

    switch (timeRange.value) {
      case 'week':
        startDate.setDate(startDate.getDate() - 7)
        break
      case 'month':
        startDate.setMonth(startDate.getMonth() - 1)
        break
      case 'quarter':
        startDate.setMonth(startDate.getMonth() - 3)
        break
      case 'year':
        startDate.setFullYear(startDate.getFullYear() - 1)
        break
    }
    return { start: startDate.toISOString().slice(0, 10), end }
  }

  const benchmarkLabels: Record<BenchmarkType, string> = {
    hs300: '沪深300',
    sz50: '上证50',
    cyb: '创业板指',
    none: '无',
  }

  function getBenchmarkLabel(): string {
    return benchmarkLabels[benchmark.value]
  }

  async function fetchData() {
    status.value = 'loading'
    const { start, end } = getDateRange()
    try {
      data.value = await performanceApi.getData({
        start,
        end,
        benchmark: benchmark.value === 'none' ? undefined : benchmark.value,
        account_id: accountId.value,
      })
      status.value = 'loaded'
    } catch {
      console.warn('Failed to fetch performance data, using mock data')
      if (isDev) {
        data.value = mockPerformanceData()
        status.value = 'loaded'
      } else {
        status.value = 'error'
      }
    }
  }

  return {
    data,
    status,
    timeRange,
    customDateRange,
    benchmark,
    accountId,
    equityCurve,
    metrics,
    drawdownCurve,
    modelContributions,
    getDateRange,
    getBenchmarkLabel,
    fetchData,
  }
})

// --- Mock data (deterministic) ---

function mockPerformanceData(): PerformanceData {
  return {
    equity_curve: generateEquityCurve(),
    metrics: {
      annualized_return: 0.185,
      sharpe_ratio: 1.32,
      max_drawdown: -0.062,
      win_rate: 0.623,
      profit_loss_ratio: 1.85,
      monthly_turnover: 0.152,
    },
    drawdown_curve: generateDrawdownCurve(),
    model_contributions: [
      {
        model: 'deepseek',
        label: 'DeepSeek',
        accuracy_label: '信号准确率',
        accuracy_value: 0.58,
        call_label: '日均调用',
        call_value: 245,
        call_unit: '次',
        cost_label: '日均成本',
        cost_value: 1.1,
        cost_unit: '¥',
      },
      {
        model: 'qwen',
        label: 'Qwen',
        accuracy_label: '分析采纳率',
        accuracy_value: 0.71,
        call_label: '日均调用',
        call_value: 89,
        call_unit: '次',
        cost_label: '日均成本',
        cost_value: 1.8,
        cost_unit: '¥',
      },
      {
        model: 'minimax',
        label: 'MiniMax',
        accuracy_label: '决策胜率',
        accuracy_value: 0.64,
        call_label: '日均调用',
        call_value: 52,
        call_unit: '次',
        cost_label: '日均成本',
        cost_value: 5.2,
        cost_unit: '¥',
      },
      {
        model: 'mirofish',
        label: 'MiroFish',
        accuracy_label: '仿真命中率',
        accuracy_value: 0.55,
        call_label: '周均触发',
        call_value: 3.2,
        call_unit: '次',
        cost_label: '周均成本',
        cost_value: 12.5,
        cost_unit: '¥',
      },
    ],
  }
}

function generateEquityCurve(): EquityPoint[] {
  const points: EquityPoint[] = []
  let portfolio = 100
  let benchmarkVal = 100
  const startDate = new Date('2026-02-24')

  for (let i = 0; i < 22; i++) {
    const d = new Date(startDate)
    d.setDate(d.getDate() + i)
    // Skip weekends
    if (d.getDay() === 0 || d.getDay() === 6) continue

    // Deterministic growth using sine curves
    const t = i / 22
    portfolio = 100 + 18.5 * t + 3 * Math.sin(t * Math.PI * 4)
    benchmarkVal = 100 + 8.2 * t + 2 * Math.sin(t * Math.PI * 3 + 0.5)

    points.push({
      date: d.toISOString().slice(0, 10),
      portfolio: Math.round(portfolio * 100) / 100,
      benchmark: Math.round(benchmarkVal * 100) / 100,
    })
  }
  return points
}

function generateDrawdownCurve(): DrawdownPoint[] {
  const points: DrawdownPoint[] = []
  const startDate = new Date('2026-02-24')
  let peak = 100

  for (let i = 0; i < 22; i++) {
    const d = new Date(startDate)
    d.setDate(d.getDate() + i)
    if (d.getDay() === 0 || d.getDay() === 6) continue

    const t = i / 22
    const value = 100 + 18.5 * t + 3 * Math.sin(t * Math.PI * 4)
    if (value > peak) peak = value
    const drawdown = (value - peak) / peak

    points.push({
      date: d.toISOString().slice(0, 10),
      drawdown: Math.round(drawdown * 10000) / 10000,
    })
  }
  return points
}
