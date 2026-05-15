<template>
  <section class="acceptance-reports">
    <header class="page-header">
      <div>
        <h2 class="page-title">验收报告</h2>
        <p class="page-subtitle">
          P0-6 §1 锁定:45 交易日滚动窗口 + 5 稳定性 + 3 策略硬门槛;
          ``can_switch_to_feishu_on`` 仅由 AcceptanceService.PASS 触发,
          严禁 env-var / CLI 绕过(P0-6 §2 红线 5)。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </header>

    <div v-if="error" class="banner banner-error">加载失败:{{ error }}</div>
    <div v-if="serviceStatus === 'unavailable'" class="banner banner-info">
      AcceptanceService 尚未接线(Phase F 集成 wiring 之后启用),当前数据均为空。
    </div>

    <article v-if="report" class="window-card">
      <header class="window-header">
        <span class="window-label">
          窗口 {{ report.window_start }} → {{ report.window_end }}
          ({{ report.trading_days_in_window }} / 45 交易日)
        </span>
        <span :class="['outcome-pill', outcomeClass]">
          {{ report.outcome }}
        </span>
      </header>
      <div class="window-grid">
        <div class="kv-row">
          <span>computed_at</span><span>{{ formatTime(report.computed_at) }}</span>
        </div>
        <div class="kv-row">
          <span>trade_date</span><span>{{ report.trade_date }}</span>
        </div>
        <div class="kv-row">
          <span>notes</span><span>{{ report.notes || '—' }}</span>
        </div>
      </div>

      <div class="switch-banner">
        <span class="switch-label">feishu_interactive 模式切换 gate</span>
        <span :class="['switch-pill', switchClass]">
          {{ canSwitch ? '可切换 (acceptance PASS)' : '不可切换' }}
        </span>
      </div>
    </article>
    <article v-else-if="serviceStatus === 'ok'" class="window-card empty">
      尚无验收报告(系统每日 16:00:30 自动生成)。
    </article>

    <section v-if="stabilityRows.length || strategyRows.length" class="metrics-section">
      <h3 class="metrics-title">5 稳定性 + 3 策略硬门槛</h3>
      <el-table :data="orderedRows" stripe size="small">
        <el-table-column label="指标" min-width="180">
          <template #default="{ row }">
            <span class="metric-label">{{ labelOf(row.name) }}</span>
            <span class="metric-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方向" width="100">
          <template #default="{ row }">
            {{ row.direction === 'at_least' ? '≥ 门槛' : '≤ 门槛' }}
          </template>
        </el-table-column>
        <el-table-column label="门槛" width="120" align="right">
          <template #default="{ row }">
            {{ formatMetricValue(row.name, row.threshold) }}
          </template>
        </el-table-column>
        <el-table-column label="本期值" width="120" align="right">
          <template #default="{ row }">
            <span :class="row.passed ? 'text-pass' : 'text-fail'">
              {{ formatMetricValue(row.name, row.value) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.passed ? 'success' : 'danger'">
              {{ row.passed ? 'PASS' : 'FAIL' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { acceptanceApi } from '@/api/acceptance'
import {
  ACCEPTANCE_METRIC_ORDER,
  METRIC_LABELS,
  type AcceptanceReportSnapshot,
} from '@/types/acceptance'

const report = ref<AcceptanceReportSnapshot | null>(null)
const canSwitch = ref(false)
const serviceStatus = ref<'ok' | 'unavailable'>('unavailable')
const loading = ref(false)
const error = ref<string | null>(null)

const POLL_INTERVAL_MS = 60_000
let pollTimer: ReturnType<typeof setInterval> | null = null

async function refresh(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const payload = await acceptanceApi.getLatest()
    report.value = payload.report
    canSwitch.value = payload.can_switch_to_feishu_on
    serviceStatus.value = payload.service_status
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load acceptance'
  } finally {
    loading.value = false
  }
}

const stabilityRows = computed(() => {
  if (!report.value) return []
  const stabilityNames = new Set(ACCEPTANCE_METRIC_ORDER.slice(0, 5))
  return report.value.metrics.filter((m) => stabilityNames.has(m.name))
})

const strategyRows = computed(() => {
  if (!report.value) return []
  const strategyNames = new Set(ACCEPTANCE_METRIC_ORDER.slice(5))
  return report.value.metrics.filter((m) => strategyNames.has(m.name))
})

const orderedRows = computed(() => {
  if (!report.value) return []
  const indexOf = (name: string): number => {
    const idx = ACCEPTANCE_METRIC_ORDER.indexOf(name)
    return idx === -1 ? Number.MAX_SAFE_INTEGER : idx
  }
  const rows = [...report.value.metrics]
  rows.sort((a, b) => indexOf(a.name) - indexOf(b.name))
  return rows
})

const outcomeClass = computed(() => {
  if (!report.value) return 'neutral'
  if (report.value.outcome === 'PASS') return 'pass'
  if (report.value.outcome === 'FAIL') return 'fail'
  if (report.value.outcome === 'PAUSED') return 'paused'
  return 'neutral'
})

const switchClass = computed(() => (canSwitch.value ? 'pass' : 'fail'))

function labelOf(name: string): string {
  return METRIC_LABELS[name] ?? name
}

function formatMetricValue(name: string, value: number): string {
  if (
    name === 'max_drawdown_pct' ||
    name === 'csi300_excess_pct'
  ) {
    return `${(value * 100).toFixed(2)}%`
  }
  if (name === 'pnl_cny') {
    return `¥${value.toFixed(2)}`
  }
  // Rates: 0..1 → percentage with 2 decimals.
  if (name.endsWith('_rate')) {
    return `${(value * 100).toFixed(2)}%`
  }
  return value.toFixed(2)
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, POLL_INTERVAL_MS)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style lang="scss" scoped>
.acceptance-reports {
  padding: $gap-md $gap-lg;
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: $gap-md;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: $text-primary;
  margin: 0;
}
.page-subtitle {
  font-size: 12px;
  color: $text-muted;
  margin: 4px 0 0;
  max-width: 720px;
}

.banner {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  border: 1px solid transparent;
}
.banner-info {
  background: rgba(68, 138, 255, 0.10);
  border-color: rgba(68, 138, 255, 0.4);
  color: $color-accent-light;
}
.banner-error {
  background: rgba(255, 23, 68, 0.12);
  border-color: rgba(255, 23, 68, 0.3);
  color: $status-red;
}

.window-card {
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: $gap-md;
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}
.window-card.empty {
  color: $text-muted;
  font-size: 12px;
  padding: $gap-md $gap-lg;
}

.window-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.window-label {
  font-size: 13px;
  color: $text-primary;
  font-weight: 600;
}

.outcome-pill,
.switch-pill {
  font-size: 12px;
  padding: 2px 12px;
  border-radius: 999px;
}
.outcome-pill.pass,
.switch-pill.pass {
  background: rgba(0, 200, 83, 0.15);
  color: $status-green;
}
.outcome-pill.fail,
.switch-pill.fail {
  background: rgba(255, 23, 68, 0.15);
  color: $status-red;
}
.outcome-pill.paused {
  background: rgba(255, 214, 0, 0.18);
  color: $color-flat;
}
.outcome-pill.neutral {
  background: rgba(142, 142, 160, 0.18);
  color: $text-muted;
}

.window-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 4px $gap-md;
}
.kv-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: $text-secondary;
  span:last-child {
    color: $text-primary;
    font-family: 'Roboto Mono', monospace;
  }
}

.switch-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: rgba(68, 138, 255, 0.08);
  border-radius: 6px;
  border: 1px dashed rgba(68, 138, 255, 0.3);
}
.switch-label {
  font-size: 12px;
  color: $text-secondary;
}

.metrics-section {
  display: flex;
  flex-direction: column;
  gap: $gap-sm;
}
.metrics-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
  margin: 0;
}
.metric-label {
  font-weight: 600;
  color: $text-primary;
  margin-right: 6px;
}
.metric-name {
  font-size: 11px;
  color: $text-muted;
  font-family: 'Roboto Mono', monospace;
}

.text-pass { color: $status-green; }
.text-fail { color: $status-red; }
</style>
