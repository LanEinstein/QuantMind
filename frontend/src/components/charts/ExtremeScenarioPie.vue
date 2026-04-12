<template>
  <div class="extreme-scenario-pie">
    <div class="chart-area">
      <v-chart :option="chartOption" autoresize @click="onChartClick" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { ExtremeScenario } from '@/types/simulation'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const COLORS_UPSIDE = ['#ff1744', '#ff6d00', '#ffd600']
const COLORS_DOWNSIDE = ['#00c853', '#00bcd4', '#7c4dff']

const props = defineProps<{
  upsideScenarios: readonly ExtremeScenario[]
  downsideScenarios: readonly ExtremeScenario[]
  allScenarios: readonly ExtremeScenario[]
}>()

const emit = defineEmits<{
  'open-scenario': [scenario: ExtremeScenario]
}>()

const baselineProbability = computed(() => {
  const sum = props.allScenarios.reduce((acc, s) => acc + s.probability, 0)
  return Math.max(0, 1 - sum)
})

const chartOption = computed(() => {
  const upsideData = props.upsideScenarios.map((s, i) => ({
    name: s.scenario,
    value: Math.round(s.probability * 100),
    itemStyle: { color: COLORS_UPSIDE[i % COLORS_UPSIDE.length] },
    _scenario: s,
  }))

  const downsideData = props.downsideScenarios.map((s, i) => ({
    name: s.scenario,
    value: Math.round(s.probability * 100),
    itemStyle: { color: COLORS_DOWNSIDE[i % COLORS_DOWNSIDE.length] },
    _scenario: s,
  }))

  // Baseline occupies the remaining probability across both series
  const halfBaseline = Math.round((baselineProbability.value / 2) * 100)

  const labelConfig = {
    color: '#a0a0b0',
    fontSize: 10,
    formatter: '{b}\n{d}%',
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 11 },
      formatter: (params: { name: string; percent: number; data: { _scenario?: ExtremeScenario } }) => {
        const name = escapeHtml(params.name)
        const s = params.data._scenario
        return s
          ? `<b>${name}</b><br/>概率: ${params.percent}%<br/>影响: ${escapeHtml(s.impact)}`
          : `<b>${name}</b><br/>概率: ${params.percent}%`
      },
    },
    series: [
      // Upside scenarios — right hemisphere
      {
        type: 'pie',
        name: 'upside',
        startAngle: 90,
        endAngle: -90,
        radius: ['40%', '70%'],
        center: ['50%', '50%'],
        label: labelConfig,
        labelLine: { lineStyle: { color: '#2a2a4a' } },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: 'rgba(255,23,68,0.4)' },
        },
        data: [
          ...upsideData,
          ...(halfBaseline > 0
            ? [{ name: '基准(上)', value: halfBaseline, itemStyle: { color: '#2a2a3a' }, _scenario: undefined }]
            : []),
        ],
      },
      // Downside scenarios — left hemisphere
      {
        type: 'pie',
        name: 'downside',
        startAngle: 90,
        endAngle: 270,
        radius: ['40%', '70%'],
        center: ['50%', '50%'],
        label: labelConfig,
        labelLine: { lineStyle: { color: '#2a2a4a' } },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,200,83,0.4)' },
        },
        data: [
          ...downsideData,
          ...(halfBaseline > 0
            ? [{ name: '基准(下)', value: halfBaseline, itemStyle: { color: '#2a2a3a' }, _scenario: undefined }]
            : []),
        ],
      },
    ],
  }
})

function onChartClick(params: unknown) {
  const p = params as { data?: { _scenario?: ExtremeScenario } }
  const scenario = p.data?._scenario
  if (scenario) {
    emit('open-scenario', scenario)
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
</style>
