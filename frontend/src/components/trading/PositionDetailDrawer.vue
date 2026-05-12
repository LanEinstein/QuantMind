<template>
  <el-drawer
    :model-value="modelValue"
    size="38%"
    direction="rtl"
    :title="title"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template v-if="position">
      <div class="detail-grid">
        <!-- Price comparison -->
        <div class="detail-row">
          <span class="detail-label">成本价</span>
          <span class="detail-val">{{ position.cost_price.toFixed(2) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">现价</span>
          <span class="detail-val" :class="pnlClass">
            {{ currentPrice.toFixed(2) }}
          </span>
        </div>
        <div class="detail-row">
          <span class="detail-label">盈亏</span>
          <span class="detail-val" :class="pnlClass">
            {{ pnlPrefix }}{{ Math.abs(position.unrealized_pnl).toFixed(2) }}
            ({{ (position.unrealized_pnl_pct * 100).toFixed(2) }}%)
          </span>
        </div>

        <!-- Stop-loss gauge -->
        <div class="gauge-section">
          <div class="detail-label">止损距离</div>
          <div class="gauge-bar">
            <div class="gauge-fill" :class="distanceClass" :style="{ width: gaugePct + '%' }" />
            <div class="gauge-marker" :style="{ left: gaugePct + '%' }" />
          </div>
          <div class="gauge-labels">
            <span>止损线 {{ position.stop_loss_line.toFixed(2) }}</span>
            <span :class="distanceClass">距离 {{ (position.stop_loss_distance * 100).toFixed(1) }}%</span>
          </div>
        </div>

        <!-- Position weight vs limit -->
        <div class="detail-row">
          <span class="detail-label">仓位占比</span>
          <span class="detail-val">{{ (position.position_pct * 100).toFixed(1) }}% / 15%</span>
        </div>
        <el-progress
          :percentage="Math.min(Math.round((position.position_pct / 0.15) * 100), 100)"
          :stroke-width="12"
          :text-inside="true"
          :format="() => (position!.position_pct * 100).toFixed(1) + '%'"
          :color="positionColor"
        />

        <!-- Risk status -->
        <div class="detail-row" style="margin-top: 12px">
          <span class="detail-label">风控状态</span>
          <el-tag :type="riskTagType" size="small" effect="dark">{{ riskLabel }}</el-tag>
        </div>

        <!-- Holdings info -->
        <div class="detail-row">
          <span class="detail-label">持仓数量</span>
          <span class="detail-val">{{ position.volume }} 股 (可用 {{ position.available_volume }})</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">市值</span>
          <span class="detail-val">{{ position.market_value.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PositionItem, RiskStatusLevel } from '@/types/trading'
import { getStockName } from '@/stores/portfolio'

const props = defineProps<{
  modelValue: boolean
  position: PositionItem | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const title = computed(() => {
  if (!props.position) return ''
  return `${props.position.code} ${getStockName(props.position.code)}`
})

const currentPrice = computed(() => {
  const p = props.position
  if (!p || p.volume === 0) return p?.cost_price ?? 0
  return p.market_value / p.volume
})

const pnlClass = computed(() => {
  const pnl = props.position?.unrealized_pnl ?? 0
  if (pnl > 0) return 'text-up'
  if (pnl < 0) return 'text-down'
  return ''
})

const pnlPrefix = computed(() => {
  return (props.position?.unrealized_pnl ?? 0) >= 0 ? '+' : '-'
})

const distanceClass = computed(() => {
  const d = props.position?.stop_loss_distance ?? 0
  if (d > 0.05) return 'distance-safe'
  if (d >= 0.02) return 'distance-warn'
  return 'distance-danger'
})

// Gauge: 0% = at stop loss, 100% = far from stop loss (>10%)
const gaugePct = computed(() => {
  const d = props.position?.stop_loss_distance ?? 0
  return Math.min(Math.round((d / 0.10) * 100), 100)
})

const positionColor = computed(() => {
  // P0-7 single-stock hard cap = 0.15; redline-check.sh locks the YAML.
  // Mirror that here so the gauge color flips to red the moment an
  // existing position drifts past the enforced cap (e.g. from intraday
  // MTM growth) rather than at the obsolete 20% threshold.
  const pct = props.position?.position_pct ?? 0
  if (pct > 0.15) return '#ff1744'
  if (pct > 0.10) return '#ffd600'
  return '#448aff'
})

const riskTagType = computed(() => {
  const map: Record<RiskStatusLevel, 'success' | 'warning' | 'danger'> = {
    normal: 'success',
    near_stop: 'warning',
    triggered: 'danger',
    over_limit: 'warning',
  }
  return map[props.position?.risk_status ?? 'normal']
})

const riskLabel = computed(() => {
  const map: Record<RiskStatusLevel, string> = {
    normal: '正常',
    near_stop: '接近止损',
    triggered: '已触发',
    over_limit: '仓位超限',
  }
  return map[props.position?.risk_status ?? 'normal']
})
</script>

<style scoped lang="scss">
.detail-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.detail-label {
  color: $text-muted;
  font-size: 13px;
}

.detail-val {
  color: $text-primary;
  font-size: 14px;
  font-weight: 600;
}

.text-up { color: $color-up; }
.text-down { color: $color-down; }

.gauge-section {
  margin: 8px 0;
}

.gauge-bar {
  position: relative;
  height: 8px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 4px;
  margin: 8px 0 4px;
  overflow: visible;
}

.gauge-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s ease;

  &.distance-safe { background: $status-green; }
  &.distance-warn { background: $status-yellow; }
  &.distance-danger { background: $status-red; }
}

.gauge-marker {
  position: absolute;
  top: -3px;
  width: 3px;
  height: 14px;
  background: $text-primary;
  border-radius: 1px;
  transform: translateX(-50%);
}

.gauge-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: $text-muted;
}

.distance-safe { color: $status-green; font-weight: 600; }
.distance-warn { color: $status-yellow; font-weight: 600; }
.distance-danger { color: $status-red; font-weight: 600; }
</style>
