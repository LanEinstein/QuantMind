<template>
  <div class="equity-curve">
    <v-chart :option="chartOption" autoresize class="chart" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EquityPoint } from '@/types/performance'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  data: readonly EquityPoint[]
  benchmarkLabel: string
}>()

const chartOption = computed(() => {
  const dates = props.data.map((d) => d.date)
  const portfolioValues = props.data.map((d) => d.portfolio)
  const benchmarkValues = props.data.map((d) => d.benchmark)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 12 },
      formatter(params: Array<{ seriesName: string; value: number; axisValue: string }>) {
        const date = params[0]?.axisValue ?? ''
        const lines = params.map(
          (p) => `${p.seriesName}: ${p.value.toFixed(2)}`,
        )
        const diff = (params[0]?.value ?? 0) - (params[1]?.value ?? 0)
        lines.push(`超额: ${diff >= 0 ? '+' : ''}${diff.toFixed(2)}`)
        return `${date}<br/>${lines.join('<br/>')}`
      },
    },
    legend: {
      data: ['QuantMind', props.benchmarkLabel],
      textStyle: { color: '#a0a0b0', fontSize: 12 },
      top: 0,
      right: 0,
    },
    grid: { left: 50, right: 16, top: 32, bottom: 24 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#6c6c80', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2a4a' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#2a2a4a', type: 'dashed' } },
      axisLabel: { color: '#6c6c80', fontSize: 10 },
    },
    series: [
      {
        name: 'QuantMind',
        type: 'line',
        data: portfolioValues,
        showSymbol: false,
        lineStyle: { color: '#448aff', width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(68, 138, 255, 0.25)' },
              { offset: 1, color: 'rgba(68, 138, 255, 0.02)' },
            ],
          },
        },
      },
      {
        name: props.benchmarkLabel,
        type: 'line',
        data: benchmarkValues,
        showSymbol: false,
        lineStyle: { color: '#ffd600', width: 1.5, type: 'dashed' },
      },
    ],
  }
})
</script>

<style lang="scss" scoped>
.equity-curve {
  width: 100%;
  height: 100%;
}

.chart {
  width: 100%;
  height: 100%;
  min-height: 240px;
}
</style>
