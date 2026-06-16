<template>
  <div class="cost-dashboard-page">
    <!-- Period Selector + Summary Cards -->
    <div class="top-bar">
      <el-radio-group v-model="store.costDays" size="small" @change="onPeriodChange">
        <el-radio-button :value="7">最近7天</el-radio-button>
        <el-radio-button :value="30">最近30天</el-radio-button>
      </el-radio-group>
    </div>

    <!-- Summary Cards -->
    <el-row :gutter="12" class="summary-cards">
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">总成本</div>
          <div class="stat-value">¥{{ totalCost }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">总请求数</div>
          <div class="stat-value">{{ totalRequests }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">日均成本</div>
          <div class="stat-value">¥{{ dailyAvg }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">月度预估</div>
          <div class="stat-value accent">¥{{ store.monthlyProjection }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Charts Row -->
    <el-row :gutter="12" class="charts-row">
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <span class="card-title">每日成本趋势</span>
          </template>
          <v-chart :option="barChartOption" autoresize class="trend-chart" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header>
            <span class="card-title">Provider成本占比</span>
          </template>
          <v-chart :option="pieChartOption" autoresize class="pie-chart" />
        </el-card>
      </el-col>
    </el-row>

    <!-- Per-Agent Table -->
    <el-card shadow="never">
      <template #header>
        <span class="card-title">按Agent成本明细</span>
      </template>
      <el-table :data="agentCostRows" stripe>
        <el-table-column prop="agent" label="Agent" width="200" />
        <el-table-column prop="provider" label="Provider" width="120">
          <template #default="{ row }">
            <el-tag :type="providerTag(row.provider)" size="small">{{ row.provider }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="requests" label="请求数" width="100" sortable />
        <el-table-column prop="prompt_tokens" label="Prompt Tokens" width="140" sortable>
          <template #default="{ row }">{{ row.prompt_tokens.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="completion_tokens" label="Completion Tokens" width="160" sortable>
          <template #default="{ row }">{{ row.completion_tokens.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="cost" label="成本 (¥)" width="120" sortable>
          <template #default="{ row }">{{ row.cost.toFixed(4) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useSettingsStore } from '@/stores/settings'

use([BarChart, PieChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const store = useSettingsStore()

const totalCost = computed(() => {
  return (store.costSummary?.total_cost_rmb ?? 0).toFixed(2)
})

const totalRequests = computed(() => {
  return (store.costSummary?.total_requests ?? 0).toLocaleString()
})

const dailyAvg = computed(() => {
  const sum = store.costSummary
  if (!sum || sum.days === 0) return '0.00'
  return (sum.total_cost_rmb / sum.days).toFixed(2)
})

function providerTag(provider: string): 'success' | 'warning' | 'info' {
  if (provider === 'deepseek') return 'success'
  if (provider === 'qwen') return 'warning'
  return 'info'
}

// Aggregate per-agent rows from entries
const agentCostRows = computed(() => {
  const entries = store.costSummary?.entries ?? []
  const map = new Map<string, {
    agent: string; provider: string; requests: number;
    prompt_tokens: number; completion_tokens: number; cost: number
  }>()

  for (const e of entries) {
    const existing = map.get(e.agent_name)
    if (existing) {
      map.set(e.agent_name, {
        ...existing,
        requests: existing.requests + e.requests,
        prompt_tokens: existing.prompt_tokens + e.prompt_tokens,
        completion_tokens: existing.completion_tokens + e.completion_tokens,
        cost: existing.cost + e.cost_rmb,
      })
    } else {
      map.set(e.agent_name, {
        agent: e.agent_name,
        provider: e.provider,
        requests: e.requests,
        prompt_tokens: e.prompt_tokens,
        completion_tokens: e.completion_tokens,
        cost: e.cost_rmb,
      })
    }
  }

  return Array.from(map.values()).sort((a, b) => b.cost - a.cost)
})

// Stacked bar chart
const barChartOption = computed(() => {
  const dailyTotals = store.dailyCostTotals
  const dates = Object.keys(dailyTotals).sort()

  // Aggregate per-provider per-day
  const entries = store.costSummary?.entries ?? []
  const providerDays: Record<string, Record<string, number>> = {}

  for (const e of entries) {
    if (!providerDays[e.provider]) providerDays[e.provider] = {}
    providerDays[e.provider][e.date] = (providerDays[e.provider][e.date] ?? 0) + e.cost_rmb
  }

  const providerColors: Record<string, string> = {
    deepseek: '#00c853',
    qwen: '#ff9100',
    kimi: '#448aff',
  }

  const series = Object.entries(providerDays).map(([provider, dayMap]) => ({
    name: provider,
    type: 'bar' as const,
    stack: 'cost',
    data: dates.map((d) => +(dayMap[d] ?? 0).toFixed(4)),
    itemStyle: { color: providerColors[provider] ?? '#888' },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' as const },
    legend: { textStyle: { color: '#a0a0b0' } },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: {
      type: 'category' as const,
      data: dates.map((d) => d.slice(5)),
      axisLabel: { color: '#a0a0b0', fontSize: 10 },
    },
    yAxis: {
      type: 'value' as const,
      name: '¥',
      axisLabel: { color: '#a0a0b0' },
      splitLine: { lineStyle: { color: '#2a2a4a' } },
    },
    series,
  }
})

// Pie chart
const pieChartOption = computed(() => {
  const byProvider = store.costByProvider
  const providerColors: Record<string, string> = {
    deepseek: '#00c853',
    qwen: '#ff9100',
    kimi: '#448aff',
  }

  const data = Object.entries(byProvider).map(([name, value]) => ({
    name,
    value: +value.toFixed(4),
    itemStyle: { color: providerColors[name] ?? '#888' },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' as const },
    series: [{
      type: 'pie' as const,
      radius: ['40%', '70%'],
      data,
      label: {
        color: '#e0e0e0',
        formatter: '{b}: ¥{c}',
      },
    }],
  }
})

function onPeriodChange() {
  store.fetchCostStats()
}

onMounted(() => {
  store.fetchCostStats()
})
</script>

<style lang="scss" scoped>
.cost-dashboard-page {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.top-bar {
  display: flex;
  align-items: center;
}

.summary-cards {
  .stat-card {
    text-align: center;
    padding: 8px 0;
  }

  .stat-label {
    font-size: 12px;
    color: $text-muted;
    margin-bottom: 4px;
  }

  .stat-value {
    font-size: 22px;
    font-weight: 600;
    color: $text-primary;

    &.accent {
      color: $color-accent;
    }
  }
}

.charts-row {
  .trend-chart {
    height: 350px;
    width: 100%;
  }

  .pie-chart {
    height: 350px;
    width: 100%;
  }
}

.card-title {
  font-weight: 600;
  font-size: 14px;
}
</style>
