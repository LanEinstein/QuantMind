<template>
  <div class="account-banner">
    <el-row :gutter="12">
      <!-- Total Assets -->
      <el-col :span="5">
        <div class="stat-card">
          <div class="stat-label">总资产</div>
          <div class="stat-value">¥{{ formatNumber(account.total_assets) }}</div>
        </div>
      </el-col>

      <!-- Today's P&L -->
      <el-col :span="5">
        <div class="stat-card">
          <div class="stat-label">总盈亏</div>
          <div class="stat-value" :class="pnlClass">
            {{ pnlPrefix }}¥{{ formatNumber(Math.abs(account.total_pnl)) }}
            <span class="stat-pct">({{ pnlPctText }})</span>
          </div>
        </div>
      </el-col>

      <!-- Position Market Value -->
      <el-col :span="5">
        <div class="stat-card">
          <div class="stat-label">持仓市值</div>
          <div class="stat-value">¥{{ formatNumber(account.market_value) }}</div>
          <el-progress
            :percentage="positionRatio"
            :stroke-width="8"
            :show-text="true"
            :format="() => positionRatio + '%'"
            color="#448aff"
            class="stat-progress"
          />
        </div>
      </el-col>

      <!-- Available Cash -->
      <el-col :span="4">
        <div class="stat-card">
          <div class="stat-label">可用资金</div>
          <div class="stat-value">¥{{ formatNumber(account.available_cash) }}</div>
          <div class="stat-sub">{{ cashRatio }}%</div>
        </div>
      </el-col>

      <!-- Authorization Mode -->
      <el-col :span="2">
        <div class="stat-card">
          <div class="stat-label">授权模式</div>
          <el-tag :type="authModeTagType" size="small" effect="dark" class="auth-mode-tag">
            {{ authModeLabel }}
          </el-tag>
        </div>
      </el-col>

      <!-- Mini Equity Curve -->
      <el-col :span="3">
        <div class="stat-card sparkline-card">
          <div class="stat-label">净值曲线 (30日)</div>
          <VChart :option="sparklineOption" autoresize class="sparkline-chart" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import type { AccountInfo } from '@/types/trading'
import { usePortfolioStore } from '@/stores/portfolio'

use([CanvasRenderer, LineChart, GridComponent])

const props = defineProps<{
  account: AccountInfo
}>()

const store = usePortfolioStore()

const positionRatio = computed(() => store.positionRatio)
const cashRatio = computed(() => store.cashRatio)

const authModeTagType = computed(() => {
  switch (store.authMode) {
    case 'semi_auto': return 'warning'
    case 'full_auto': return 'danger'
    default: return 'info'
  }
})

const authModeLabel = computed(() => {
  const labels: Record<string, string> = {
    suggestion: '建议模式',
    semi_auto: '半自动',
    full_auto: '全自动',
  }
  return labels[store.authMode] ?? '建议模式'
})

const pnlClass = computed(() => {
  if (props.account.total_pnl > 0) return 'text-up'
  if (props.account.total_pnl < 0) return 'text-down'
  return ''
})

const pnlPrefix = computed(() => (props.account.total_pnl >= 0 ? '+' : '-'))

const pnlPctText = computed(() => {
  const pct = props.account.total_pnl_pct * 100
  const prefix = pct >= 0 ? '+' : ''
  return prefix + pct.toFixed(2) + '%'
})

function formatNumber(n: number): string {
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Deterministic 30-day equity curve
const sparklineData = computed(() => {
  const base = props.account.initial_capital
  const points: number[] = []
  for (let i = 0; i < 30; i++) {
    const t = i / 29
    const value = base * (1 + 0.04 * Math.sin(t * Math.PI * 2.5) + 0.025 * t)
    points.push(Math.round(value))
  }
  return points
})

const sparklineOption = computed(() => ({
  grid: { top: 4, right: 4, bottom: 4, left: 4 },
  xAxis: { show: false, type: 'category' as const },
  yAxis: { show: false, type: 'value' as const, min: 'dataMin', max: 'dataMax' },
  series: [
    {
      type: 'line' as const,
      data: sparklineData.value,
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 2, color: '#448aff' },
      areaStyle: { color: 'rgba(68, 138, 255, 0.15)' },
    },
  ],
}))
</script>

<style scoped lang="scss">
.account-banner {
  margin-bottom: $gap-md;
}

.stat-card {
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: $gap-md;
  height: 100%;
}

.stat-label {
  color: $text-muted;
  font-size: 12px;
  margin-bottom: 4px;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  color: $text-primary;
}

.stat-pct {
  font-size: 13px;
  font-weight: 400;
}

.stat-sub {
  color: $text-secondary;
  font-size: 12px;
  margin-top: 4px;
}

.stat-progress {
  margin-top: 8px;
}

.text-up {
  color: $color-up;
}

.text-down {
  color: $color-down;
}

.sparkline-card {
  padding-bottom: 8px;
}

.sparkline-chart {
  width: 100%;
  height: 60px;
}

.auth-mode-tag {
  margin-top: 8px;
}
</style>
