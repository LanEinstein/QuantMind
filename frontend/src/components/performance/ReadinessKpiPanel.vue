<template>
  <el-card shadow="never" class="readiness-kpi-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">实盘就绪度 · EquityPoint KPI</span>
        <div class="header-right">
          <el-tag
            v-if="acceptance"
            :type="acceptance.can_switch_to_feishu_on ? 'success' : 'info'"
            size="small"
            effect="dark"
          >
            {{ acceptance.can_switch_to_feishu_on ? '可切换实盘' : '尚不可切换' }}
          </el-tag>
          <el-button text size="small" :loading="loading" @click="reload">刷新</el-button>
        </div>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <template v-else>
      <!-- KPI tiles — EquityPoint-sourced (AD-001) -->
      <div class="kpi-grid">
        <div class="kpi-tile">
          <span class="kpi-label">总收益</span>
          <span class="kpi-value" :class="pnlClass(kpis.total_return)">
            {{ pct(kpis.total_return) }}
          </span>
        </div>
        <div class="kpi-tile">
          <span class="kpi-label">
            年化
            <el-tooltip
              v-if="!kpis.annualized_reliable"
              content="样本不足 45 交易日,年化仅供参考"
              placement="top"
            >
              <span class="caveat">*</span>
            </el-tooltip>
          </span>
          <span
            class="kpi-value"
            :class="[pnlClass(kpis.annualized_return), { faded: !kpis.annualized_reliable }]"
          >
            {{ pct(kpis.annualized_return) }}
          </span>
        </div>
        <div class="kpi-tile">
          <span class="kpi-label">沪深300 超额</span>
          <span
            class="kpi-value"
            :class="kpis.hs300_excess === null ? '' : pnlClass(kpis.hs300_excess)"
          >
            {{ kpis.hs300_excess === null ? '—' : pct(kpis.hs300_excess) }}
          </span>
        </div>
        <div class="kpi-tile">
          <span class="kpi-label">最大回撤</span>
          <span class="kpi-value text-down">{{ pct(kpis.max_drawdown) }}</span>
        </div>
        <div class="kpi-tile">
          <span class="kpi-label">夏普</span>
          <span class="kpi-value">{{ kpis.sharpe_ratio.toFixed(2) }}</span>
        </div>
        <div class="kpi-tile">
          <span class="kpi-label">样本/分段</span>
          <span class="kpi-value">
            {{ kpis.sample_trading_days }}d / {{ kpis.policy_segment_count }}段
          </span>
        </div>
      </div>

      <p class="kpi-foot">
        EquityPoint 真相源(非成交净额派生)。
        <span v-if="!kpis.annualized_reliable">短窗年化已降权(*)。</span>
        <span v-if="repositoryStatus !== 'ok'">权益快照仓库未接线,KPI 为空态。</span>
      </p>

      <!-- 8-gate readiness gauge -->
      <div class="gauge-section">
        <div class="gauge-head">
          <span class="gauge-title">验收 8 门</span>
          <el-tag :type="outcomeType" size="small" effect="plain">
            {{ outcomeLabel }}
          </el-tag>
          <span v-if="acceptance?.report" class="gauge-window">
            {{ acceptance.report.window_start }} → {{ acceptance.report.window_end }}
            · {{ acceptance.report.trading_days_in_window }}/45 交易日
          </span>
        </div>
        <div v-if="gates.length" class="gauge-grid">
          <div
            v-for="g in gates"
            :key="g.name"
            class="gate-chip"
            :class="g.passed ? 'gate-pass' : 'gate-fail'"
          >
            <span class="gate-name">{{ metricLabel(g.name) }}</span>
            <span class="gate-mark">{{ g.passed ? '✓' : '✗' }}</span>
          </div>
        </div>
        <p v-else class="placeholder-text">
          {{ acceptance?.service_status === 'ok'
            ? '验收尚在预热(滚动窗口未填满)。'
            : '验收服务未接线。' }}
        </p>
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { performanceApi } from '@/api/performance'
import { acceptanceApi } from '@/api/acceptance'
import {
  ACCEPTANCE_METRIC_ORDER,
  METRIC_LABELS,
  type AcceptanceLatestPayload,
  type AcceptanceMetricRow,
} from '@/types/acceptance'
import type { EquityKpis, EquityKpisPayload } from '@/types/performance'

const EMPTY_KPIS: EquityKpis = {
  total_return: 0,
  annualized_return: 0,
  annualized_reliable: false,
  max_drawdown: 0,
  sharpe_ratio: 0,
  hs300_excess: null,
  sample_trading_days: 0,
  policy_segment_count: 0,
  data_quality: {},
  latest_total_equity: 0,
}

const kpisPayload = ref<EquityKpisPayload | null>(null)
const acceptance = ref<AcceptanceLatestPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const kpis = computed<EquityKpis>(() => kpisPayload.value?.kpis ?? EMPTY_KPIS)
const repositoryStatus = computed(() => kpisPayload.value?.repository_status ?? 'unavailable')

const gates = computed<readonly AcceptanceMetricRow[]>(() => {
  const report = acceptance.value?.report
  if (!report) return []
  const byName = new Map(report.metrics.map((m) => [m.name, m]))
  return ACCEPTANCE_METRIC_ORDER.map((n) => byName.get(n)).filter(
    (m): m is AcceptanceMetricRow => m !== undefined,
  )
})

const outcomeLabel = computed(() => acceptance.value?.report?.outcome ?? '无数据')
const outcomeType = computed<'success' | 'danger' | 'warning' | 'info'>(() => {
  switch (acceptance.value?.report?.outcome) {
    case 'PASS':
      return 'success'
    case 'FAIL':
      return 'danger'
    case 'PAUSED':
      return 'warning'
    default:
      return 'info'
  }
})

function pct(value: number): string {
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${(value * 100).toFixed(2)}%`
}

function pnlClass(value: number): string {
  if (value > 0) return 'text-up'
  if (value < 0) return 'text-down'
  return ''
}

function metricLabel(name: string): string {
  return METRIC_LABELS[name] ?? name
}

async function reload(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [k, a] = await Promise.all([
      performanceApi.getEquityKpis(),
      acceptanceApi.getLatest(),
    ])
    kpisPayload.value = k
    acceptance.value = a
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load readiness KPIs'
  } finally {
    loading.value = false
  }
}

onMounted(reload)

defineExpose({ reload })
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: $gap-sm;
}
.kpi-tile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  background: $bg-card;
}
.kpi-label {
  font-size: 12px;
  color: $text-secondary;
}
.kpi-value {
  font-size: 18px;
  font-weight: 600;
  color: $text-primary;
}
.kpi-value.faded {
  opacity: 0.6;
}
.caveat {
  color: $status-yellow;
  cursor: help;
}
.text-up {
  color: $color-up;
}
.text-down {
  color: $color-down;
}

.kpi-foot {
  font-size: 11px;
  color: $text-muted;
  margin: $gap-sm 0 0;
}

.gauge-section {
  margin-top: $gap-md;
  border-top: 1px solid $border-color;
  padding-top: $gap-sm;
}
.gauge-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: $gap-sm;
}
.gauge-title {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}
.gauge-window {
  font-size: 11px;
  color: $text-muted;
}
.gauge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 6px;
}
.gate-chip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 12px;
}
.gate-pass {
  background: rgba(0, 200, 83, 0.12);
  color: $status-green;
}
.gate-fail {
  background: rgba(255, 23, 68, 0.14);
  color: $status-red;
}
.gate-mark {
  font-weight: 700;
}
.placeholder-text {
  color: $text-muted;
  font-size: 12px;
  padding: 8px 0;
}
.error-text {
  color: $status-red;
}
</style>
