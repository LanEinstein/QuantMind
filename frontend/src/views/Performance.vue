<template>
  <div class="performance-layout">
    <!-- Header Controls -->
    <div class="header-controls">
      <div class="control-group">
        <span class="control-label">时间范围</span>
        <el-radio-group v-model="store.timeRange" size="small" @change="onFilterChange">
          <el-radio-button value="week">本周</el-radio-button>
          <el-radio-button value="month">本月</el-radio-button>
          <el-radio-button value="quarter">本季</el-radio-button>
          <el-radio-button value="year">本年</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="store.timeRange === 'custom'"
          v-model="dateRange"
          type="daterange"
          size="small"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          @change="onCustomDateChange"
        />
      </div>
      <div class="control-group">
        <span class="control-label">对照基准</span>
        <el-select v-model="store.benchmark" size="small" @change="onFilterChange">
          <el-option value="hs300" label="沪深300" />
          <el-option value="sz50" label="上证50" />
          <el-option value="cyb" label="创业板指" />
          <el-option value="none" label="无" />
        </el-select>
      </div>
      <div class="control-group">
        <span class="control-label">账户</span>
        <el-select v-model="store.accountId" size="small" @change="onFilterChange">
          <el-option value="default" label="策略A (默认)" />
          <el-option value="conservative" label="策略B (保守)" />
        </el-select>
      </div>
    </div>

    <!-- Zone A + B: Equity Curve (left) + Core Metrics (right) -->
    <el-row :gutter="12" class="zone-ab">
      <el-col :span="16">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">净值曲线</span>
          </template>
          <EquityCurve
            :data="store.equityCurve"
            :benchmark-label="store.getBenchmarkLabel()"
          />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="metrics-card">
          <template #header>
            <span class="card-title">核心指标</span>
          </template>
          <div class="metrics-grid" v-if="store.metrics">
            <div class="metric-item">
              <span class="metric-value" :class="store.metrics.annualized_return >= 0 ? 'positive' : 'negative'">
                {{ store.metrics.annualized_return >= 0 ? '+' : '' }}{{ (store.metrics.annualized_return * 100).toFixed(1) }}%
              </span>
              <span class="metric-trend" :class="store.metrics.annualized_return >= 0 ? 'positive' : 'negative'">
                {{ store.metrics.annualized_return >= 0 ? '↗' : '↘' }}
              </span>
              <span class="metric-label">年化收益率</span>
            </div>
            <div class="metric-item">
              <span class="metric-value neutral">{{ store.metrics.sharpe_ratio.toFixed(2) }}</span>
              <span class="metric-label">Sharpe比率</span>
            </div>
            <div class="metric-item">
              <span class="metric-value negative">{{ (store.metrics.max_drawdown * 100).toFixed(1) }}%</span>
              <span class="metric-label">最大回撤</span>
            </div>
            <div class="metric-item">
              <span class="metric-value" :class="store.metrics.win_rate >= 0.5 ? 'positive' : 'negative'">
                {{ (store.metrics.win_rate * 100).toFixed(1) }}%
              </span>
              <span class="metric-label">胜率</span>
            </div>
            <div class="metric-item">
              <span class="metric-value" :class="store.metrics.profit_loss_ratio >= 1 ? 'positive' : 'negative'">
                {{ store.metrics.profit_loss_ratio.toFixed(2) }}
              </span>
              <span class="metric-label">盈亏比</span>
            </div>
            <div class="metric-item">
              <span class="metric-value neutral">{{ (store.metrics.monthly_turnover * 100).toFixed(1) }}%</span>
              <span class="metric-label">月换手率</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Zone C + D: Drawdown (left) + Model Contribution (right) -->
    <el-row :gutter="12" class="zone-cd">
      <el-col :span="16">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <span class="card-title">回撤曲线</span>
          </template>
          <DrawdownChart :data="store.drawdownCurve" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="model-card">
          <template #header>
            <span class="card-title">模型贡献度分析</span>
          </template>
          <ModelContribution :data="store.modelContributions" />
        </el-card>
      </el-col>
    </el-row>

    <!-- Export Buttons -->
    <div class="export-bar">
      <el-button size="small" @click="onExport('daily')">导出日报</el-button>
      <el-button size="small" @click="onExport('weekly')">导出周报</el-button>
      <el-button size="small" @click="onExport('monthly')">导出月报</el-button>
    </div>

    <!-- Loading overlay -->
    <div v-if="store.status === 'loading'" class="loading-overlay">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePerformanceStore } from '@/stores/performance'
import EquityCurve from '@/components/charts/EquityCurve.vue'
import DrawdownChart from '@/components/charts/DrawdownChart.vue'
import ModelContribution from '@/components/charts/ModelContribution.vue'

const store = usePerformanceStore()

// Local reactive for date-picker v-model (el-date-picker needs array binding)
const dateRange = ref<[string, string] | null>(null)

onMounted(async () => {
  await store.fetchData()
})

async function onFilterChange() {
  await store.fetchData()
}

async function onCustomDateChange(val: [string, string] | null) {
  if (val) {
    store.customDateRange = val
    await store.fetchData()
  }
}

async function onExport(type: 'daily' | 'weekly' | 'monthly') {
  const labels = { daily: '日报', weekly: '周报', monthly: '月报' } as const
  ElMessage.info(`正在生成${labels[type]}...`)
  // Export would call performanceApi.exportReport(type) in production
}
</script>

<style lang="scss" scoped>
.performance-layout {
  padding: $gap-md;
  position: relative;
  min-height: 100%;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: $gap-lg;
  margin-bottom: $gap-md;
  flex-wrap: wrap;
}

.control-group {
  display: flex;
  align-items: center;
  gap: $gap-sm;
}

.control-label {
  font-size: 12px;
  color: $text-muted;
  white-space: nowrap;
}

.zone-ab,
.zone-cd {
  margin-bottom: $gap-md;
}

.chart-card {
  height: 320px;
  background: $bg-card;
  border-color: $border-color;

  :deep(.el-card__body) {
    height: calc(100% - 44px);
    padding: 8px 12px;
  }
}

.metrics-card {
  height: 320px;
  background: $bg-card;
  border-color: $border-color;
}

.model-card {
  height: 320px;
  background: $bg-card;
  border-color: $border-color;

  :deep(.el-card__body) {
    height: calc(100% - 44px);
    overflow-y: auto;
  }
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

// Core metrics 2x3 grid
.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 4px 0;
}

.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 8px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  border: 1px solid $border-color;
}

.metric-value {
  font-size: 22px;
  font-weight: 700;
  font-family: 'Roboto Mono', monospace;
  line-height: 1.2;

  &.positive { color: $color-up; }
  &.negative { color: $color-down; }
  &.neutral { color: $color-accent; }
}

.metric-trend {
  font-size: 14px;
  margin-top: 2px;

  &.positive { color: $color-up; }
  &.negative { color: $color-down; }
}

.metric-label {
  font-size: 11px;
  color: $text-muted;
  margin-top: 4px;
}

// Export bar
.export-bar {
  display: flex;
  gap: $gap-sm;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba($bg-primary, 0.6);
  z-index: 10;
  border-radius: $border-radius;
}
</style>
