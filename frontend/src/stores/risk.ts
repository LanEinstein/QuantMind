/** Pinia store for risk control center state (read-only per P1-5 §2). */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  RiskStatus,
  RiskRadarData,
  RiskConfig,
  RiskEvent,
  RiskEventLevel,
  RiskStoreStatus,
} from '@/types/risk'
import { riskApi } from '@/api/risk'

export const useRiskStore = defineStore('risk', () => {
  const riskStatus = ref<RiskStatus | null>(null)
  const radarData = ref<RiskRadarData | null>(null)
  const config = ref<RiskConfig | null>(null)
  const events = ref<RiskEvent[]>([])
  const status = ref<RiskStoreStatus>('idle')
  const eventLevelFilter = ref<RiskEventLevel | 'all'>('all')
  const eventDateFilter = ref<string>('')

  const isDev = import.meta.env.DEV

  const systemStatusLabel = computed(() => {
    const labels: Record<string, string> = {
      normal: '正常运行',
      warning: '预警',
      circuit_breaker: '熔断',
    }
    return labels[riskStatus.value?.system_status ?? 'normal'] ?? '未知'
  })

  const systemStatusIcon = computed(() => {
    const icons: Record<string, string> = {
      normal: '🟢',
      warning: '🟡',
      circuit_breaker: '🔴',
    }
    return icons[riskStatus.value?.system_status ?? 'normal'] ?? '⚪'
  })

  const runModeLabel = computed(() => {
    const mode = riskStatus.value?.run_mode
    if (!mode) return 'simulation_auto'
    return mode.feishu_interactive
      ? 'simulation_auto + 飞书叠加'
      : 'simulation_auto'
  })

  const filteredEvents = computed(() => {
    let result = events.value
    if (eventLevelFilter.value !== 'all') {
      result = result.filter((e) => e.level === eventLevelFilter.value)
    }
    if (eventDateFilter.value) {
      result = result.filter((e) => e.timestamp.startsWith(eventDateFilter.value))
    }
    return result
  })

  async function fetchStatus(): Promise<boolean> {
    try {
      riskStatus.value = await riskApi.getStatus()
      return true
    } catch {
      if (isDev) {
        riskStatus.value = mockRiskStatus()
      }
      return isDev
    }
  }

  async function fetchRadarData(): Promise<boolean> {
    try {
      radarData.value = await riskApi.getRadarData()
      return true
    } catch {
      if (isDev) {
        radarData.value = mockRadarData()
      }
      return isDev
    }
  }

  async function fetchConfig(): Promise<boolean> {
    try {
      config.value = await riskApi.getConfig()
      return true
    } catch {
      if (isDev) {
        config.value = mockRiskConfig()
      }
      return isDev
    }
  }

  async function fetchEvents(): Promise<boolean> {
    try {
      events.value = await riskApi.getEvents({
        level: eventLevelFilter.value === 'all' ? undefined : eventLevelFilter.value,
        start_date: eventDateFilter.value || undefined,
      })
      return true
    } catch {
      if (isDev) {
        events.value = mockRiskEvents()
      }
      return isDev
    }
  }

  async function fetchAll() {
    status.value = 'loading'
    const results = await Promise.all([
      fetchStatus(),
      fetchRadarData(),
      fetchConfig(),
      fetchEvents(),
    ])
    const allFailed = results.every((ok) => !ok)
    status.value = allFailed ? 'error' : 'loaded'
  }

  return {
    riskStatus,
    radarData,
    config,
    events,
    status,
    eventLevelFilter,
    eventDateFilter,
    systemStatusLabel,
    systemStatusIcon,
    runModeLabel,
    filteredEvents,
    fetchStatus,
    fetchRadarData,
    fetchConfig,
    fetchEvents,
    fetchAll,
  }
})

function mockRiskStatus(): RiskStatus {
  return {
    system_status: 'normal',
    run_mode: { simulation_auto: true, feishu_interactive: false },
    stop_loss_triggers_today: 0,
    circuit_breaker_triggered: false,
    llm_intercepts_today: 2,
  }
}

function mockRadarData(): RiskRadarData {
  return {
    total_position_pct: 78,
    total_position_limit: 80,
    max_single_stock_pct: 15,
    max_single_stock_limit: 20,
    industry_concentration_pct: 35,
    industry_concentration_limit: 40,
    daily_loss_pct: 0.5,
    daily_loss_limit: 3,
    stock_count: 4,
    stock_count_limit: 10,
  }
}

function mockRiskConfig(): RiskConfig {
  return {
    single_stock_limit: 20,
    total_position_limit: 80,
    stop_loss_threshold: -8,
    circuit_breaker_threshold: -3,
    llm_timeout_seconds: 30,
    llm_max_consecutive_failures: 3,
    price_deviation_limit: 5,
  }
}

function mockRiskEvents(): RiskEvent[] {
  return [
    {
      id: 'evt-001',
      timestamp: '2026-03-26T14:32:00Z',
      level: 'warning',
      description: 'LLM指令校验: 拦截异常价格委托 (600519 @¥99999)',
      action_taken: '已拦截，通知用户',
    },
    {
      id: 'evt-002',
      timestamp: '2026-03-26T13:45:00Z',
      level: 'warning',
      description: 'LLM指令校验: DeepSeek返回异常交易量建议 (000001 买入50000股)',
      action_taken: '已拦截，降级为人工审核',
    },
    {
      id: 'evt-003',
      timestamp: '2026-03-26T11:15:00Z',
      level: 'info',
      description: '仓位预警: 总仓位达78%, 接近80%上限',
      action_taken: '已发送预警通知',
    },
    {
      id: 'evt-004',
      timestamp: '2026-03-26T10:30:00Z',
      level: 'success',
      description: '风控检查: 新建仓 601318 中国平安 通过所有风控规则',
      action_taken: '放行',
    },
    {
      id: 'evt-005',
      timestamp: '2026-03-26T09:35:00Z',
      level: 'success',
      description: '日初检查: 所有风控规则正常',
      action_taken: '系统就绪',
    },
    {
      id: 'evt-006',
      timestamp: '2026-03-25T14:50:00Z',
      level: 'critical',
      description: '日内亏损达-2.8%, 接近-3%熔断阈值',
      action_taken: '已暂停新建仓操作',
    },
    {
      id: 'evt-007',
      timestamp: '2026-03-25T11:20:00Z',
      level: 'info',
      description: 'LLM健康检查: DeepSeek API响应延迟升高 (平均2.3秒)',
      action_taken: '已记录，未触发阈值',
    },
    {
      id: 'evt-008',
      timestamp: '2026-03-25T09:35:00Z',
      level: 'success',
      description: '日初检查: 所有风控规则正常',
      action_taken: '系统就绪',
    },
  ]
}
