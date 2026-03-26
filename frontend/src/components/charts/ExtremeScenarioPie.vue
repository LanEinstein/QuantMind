<template>
  <div class="extreme-scenario-pie">
    <div class="chart-area">
      <v-chart :option="chartOption" autoresize @click="onChartClick" />
    </div>
    <transition name="fade">
      <div v-if="selectedScenario" class="scenario-detail">
        <div class="detail-header">
          <span class="detail-name">{{ selectedScenario.scenario }}</span>
          <el-tag type="warning" size="small" effect="dark">
            {{ Math.round(selectedScenario.probability * 100) }}%
          </el-tag>
          <el-button
            :icon="Close"
            circle
            size="small"
            class="detail-close"
            @click="selectedScenario = null"
          />
        </div>
        <div class="detail-impact">
          <span class="impact-label">市场影响:</span>
          <span class="impact-value">{{ selectedScenario.impact }}</span>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { ExtremeScenario } from '@/types/simulation'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

const COLORS = ['#ff1744', '#ff9100', '#ffd600', '#00c853', '#448aff', '#7c4dff']

const props = defineProps<{
  scenarios: readonly ExtremeScenario[]
}>()

const selectedScenario = ref<ExtremeScenario | null>(null)

// Compute baseline probability (remainder after extreme scenarios)
const baselineProbability = computed(() => {
  const sum = props.scenarios.reduce((acc, s) => acc + s.probability, 0)
  return Math.max(0, 1 - sum)
})

const chartOption = computed(() => {
  const data = [
    ...props.scenarios.map((s, i) => ({
      name: s.scenario,
      value: Math.round(s.probability * 100),
      itemStyle: { color: COLORS[i % COLORS.length] },
      _scenario: s,
    })),
    ...(baselineProbability.value > 0
      ? [
          {
            name: '基准情景',
            value: Math.round(baselineProbability.value * 100),
            itemStyle: { color: '#424242' },
            _scenario: null,
          },
        ]
      : []),
  ]

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 11 },
      formatter: (params: { name: string; percent: number; data: { _scenario: ExtremeScenario | null } }) => {
        const impact = params.data._scenario?.impact
        return impact
          ? `<b>${params.name}</b><br/>概率: ${params.percent}%<br/>影响: ${impact}`
          : `<b>${params.name}</b><br/>概率: ${params.percent}%`
      },
    },
    legend: {
      orient: 'vertical',
      right: 8,
      top: 'center',
      textStyle: { color: '#a0a0b0', fontSize: 11 },
    },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['35%', '50%'],
        label: {
          color: '#e0e0e0',
          fontSize: 10,
          formatter: '{b}\n{d}%',
        },
        labelLine: {
          lineStyle: { color: '#2a2a4a' },
        },
        emphasis: {
          label: { fontSize: 12, fontWeight: 'bold' },
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0, 0, 0, 0.3)',
          },
        },
        data,
      },
    ],
  }
})

function onChartClick(params: unknown) {
  const p = params as { data?: { _scenario?: ExtremeScenario | null } }
  const scenario = p.data?._scenario
  if (scenario) {
    selectedScenario.value = scenario
  }
}
</script>

<style scoped lang="scss">
.extreme-scenario-pie {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chart-area {
  flex: 1;
  min-height: 200px;
}

.scenario-detail {
  padding: 12px;
  background: rgba(255, 255, 255, 0.04);
  border-left: 3px solid $color-accent;
  border-radius: 4px;
  margin-top: $gap-sm;
}

.detail-header {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  margin-bottom: $gap-sm;
}

.detail-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}

.detail-close {
  margin-left: auto;
}

.detail-impact {
  font-size: 12px;
  color: $text-secondary;
}

.impact-label {
  color: $text-muted;
  margin-right: 4px;
}

.impact-value {
  color: $text-primary;
}
</style>
