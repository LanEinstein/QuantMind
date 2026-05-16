<template>
  <section class="cost-breakdown">
    <header class="page-header">
      <div>
        <h2 class="page-title">成本拆解面板</h2>
        <p class="page-subtitle">
          P1-7 §1.7 锁定:LLM 预算分解 / 三档软门(50/80/100%)/ 唯一日 ¥20 硬熔。
          5min 自动刷新;cost API 全部 GET-only(P1-5 §2 红线 1)。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </header>

    <article v-if="error" class="banner banner-error">加载失败:{{ error }}</article>

    <article v-if="unavailable" class="banner banner-info">
      cost_guard 尚未接线(Redis 或 LLMRouter 未就绪),当前数据均为空。
    </article>

    <section v-if="budget?.daily" class="budget-row">
      <article class="budget-card">
        <header class="budget-header">每日 LLM 预算(¥20 hard)</header>
        <div class="kv-row">
          <span>已花</span><span>¥{{ formatRmb(budget.daily.spent_today) }}</span>
        </div>
        <div class="kv-row">
          <span>剩余</span><span>¥{{ formatRmb(budget.daily.remaining) }}</span>
        </div>
        <div class="kv-row">
          <span>软门(¥14 / 70%)</span>
          <span>¥{{ formatRmb(budget.daily.soft_ceiling) }}</span>
        </div>
        <div class="kv-row">
          <span>状态</span>
          <span :class="['pill', statusClass(budget.daily.status)]">
            {{ budget.daily.status }}
          </span>
        </div>
      </article>

      <article class="budget-card">
        <header class="budget-header">每月软预算(¥440 soft, 三档)</header>
        <div class="kv-row">
          <span>本月已花</span><span>¥{{ formatRmb(budget.monthly?.spent_month ?? 0) }}</span>
        </div>
        <div class="kv-row">
          <span>占比</span><span>{{ formatPercent(budget.monthly?.fraction ?? 0) }}</span>
        </div>
        <div class="kv-row">
          <span>已触发档</span>
          <span>{{ thresholdLabel(budget.monthly?.threshold_reached) }}</span>
        </div>
        <div class="kv-row">
          <span>状态</span>
          <span :class="['pill', monthlyPillClass(budget.monthly?.status)]">
            {{ budget.monthly?.status ?? '—' }}
          </span>
        </div>
      </article>

      <article class="budget-card">
        <header class="budget-header">Kimi 每日上限(¥4)</header>
        <div class="kv-row">
          <span>已花</span><span>¥{{ formatRmb(budget.kimi?.spent_today ?? 0) }}</span>
        </div>
        <div class="kv-row">
          <span>剩余</span><span>¥{{ formatRmb(budget.kimi?.remaining ?? 0) }}</span>
        </div>
        <div class="kv-row">
          <span>状态</span>
          <span :class="['pill', statusClass(budget.kimi?.status ?? 'ok')]">
            {{ budget.kimi?.status ?? '—' }}
          </span>
        </div>
      </article>
    </section>

    <section v-if="breakdown?.daily_totals" class="breakdown-section">
      <h3 class="section-title">最近 {{ breakdown.days }} 天日累计</h3>
      <el-table :data="dailyRows" stripe size="small">
        <el-table-column prop="date" label="日期" width="160" />
        <el-table-column label="总花费" min-width="120" align="right">
          <template #default="{ row }">¥{{ formatRmb(row.cost) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="breakdown?.by_provider" class="breakdown-section">
      <h3 class="section-title">按 provider 分布</h3>
      <el-table :data="providerRows" stripe size="small">
        <el-table-column prop="provider" label="provider" width="200" />
        <el-table-column label="累计花费" min-width="120" align="right">
          <template #default="{ row }">¥{{ formatRmb(row.cost) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="softDegrade" class="breakdown-section">
      <h3 class="section-title">软降级 Flag</h3>
      <div class="kv-row">
        <span>Kimi 升级是否被冻结</span>
        <span :class="softDegrade.kimi_escalation_blocked ? 'pill-warning' : 'pill-success'">
          {{ softDegrade.kimi_escalation_blocked ? '已冻结' : '正常' }}
        </span>
      </div>
      <div class="kv-row">
        <span>每月里程碑</span>
        <span>{{ thresholdLabel(softDegrade.monthly_threshold_reached ?? null) }}</span>
      </div>
    </section>

    <p class="footer-hint">
      5 分钟自动轮询。下一次刷新:{{ countdownLabel }}。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ElButton, ElTable, ElTableColumn } from 'element-plus'
import {
  costApi,
  type CostBudgetPayload,
  type CostBreakdownPayload,
  type CostSoftDegradePayload,
} from '@/api/cost'

const POLL_INTERVAL_MS = 5 * 60 * 1000

const loading = ref(false)
const error = ref<string | null>(null)
const budget = ref<CostBudgetPayload | null>(null)
const breakdown = ref<CostBreakdownPayload | null>(null)
const softDegrade = ref<CostSoftDegradePayload | null>(null)
const nextRefreshAt = ref<number>(Date.now() + POLL_INTERVAL_MS)
const _tick = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null
let countdownTimer: ReturnType<typeof setInterval> | null = null

const unavailable = computed(
  () =>
    budget.value?.status === 'unavailable' ||
    breakdown.value?.status === 'unavailable',
)

const dailyRows = computed(() => {
  const map = breakdown.value?.daily_totals ?? {}
  return Object.entries(map).map(([date, cost]) => ({ date, cost }))
})

const providerRows = computed(() => {
  const map = breakdown.value?.by_provider ?? {}
  return Object.entries(map).map(([provider, cost]) => ({ provider, cost }))
})

const countdownLabel = computed(() => {
  // touch _tick so Vue re-evaluates this computed when countdownTimer fires
  void _tick.value
  const remainingMs = Math.max(0, nextRefreshAt.value - Date.now())
  const seconds = Math.floor(remainingMs / 1000)
  const minutes = Math.floor(seconds / 60)
  const secs = seconds - minutes * 60
  return `${minutes}m${secs.toString().padStart(2, '0')}s`
})

function formatRmb(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '0.0000'
  return value.toFixed(4)
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '0.00%'
  return (value * 100).toFixed(2) + '%'
}

function thresholdLabel(threshold: number | null | undefined): string {
  if (threshold == null) return '—'
  return Math.round(threshold * 100) + '%'
}

function statusClass(status: string): string {
  if (status === 'hard_breach') return 'pill-danger'
  if (status === 'soft_breach') return 'pill-warning'
  return 'pill-success'
}

function monthlyPillClass(status: string | undefined): string {
  if (!status) return 'pill-success'
  if (status === 'threshold_100') return 'pill-danger'
  if (status === 'threshold_80') return 'pill-warning'
  if (status === 'threshold_50') return 'pill-info'
  return 'pill-success'
}

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const [b, br, sd] = await Promise.all([
      costApi.budget(),
      costApi.breakdown(7),
      costApi.softDegrade(),
    ])
    budget.value = b
    breakdown.value = br
    softDegrade.value = sd
    nextRefreshAt.value = Date.now() + POLL_INTERVAL_MS
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, POLL_INTERVAL_MS)
  countdownTimer = setInterval(() => {
    _tick.value++
  }, 1000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (countdownTimer) clearInterval(countdownTimer)
})
</script>

<style scoped>
.cost-breakdown {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.page-subtitle {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.banner {
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 13px;
}

.banner-info {
  background: var(--el-color-info-light-9);
  border-left: 4px solid var(--el-color-info);
  color: var(--el-color-info);
}

.banner-error {
  background: var(--el-color-danger-light-9);
  border-left: 4px solid var(--el-color-danger);
  color: var(--el-color-danger);
}

.budget-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.budget-card {
  flex: 1 1 280px;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
}

.budget-header {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.kv-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

.kv-row:last-child {
  border-bottom: none;
}

.section-title {
  margin: 12px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.breakdown-section {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
}

.pill {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
}

.pill-success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.pill-warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.pill-danger {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.pill-info {
  background: var(--el-color-info-light-9);
  color: var(--el-color-info);
}

.footer-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 0;
}
</style>
