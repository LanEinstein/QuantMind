<template>
  <div class="drawdown-chart">
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
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { DrawdownPoint } from '@/types/performance'

use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  data: readonly DrawdownPoint[]
  circuitBreakerLevel?: number
}>(), {
  circuitBreakerLevel: -0.03,
})

const chartOption = computed(() => {
  const dates = props.data.map((d) => d.date)
  const values = props.data.map((d) => Math.round(d.drawdown * 10000) / 100)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 12 },
      formatter(params: Array<{ value: number; axisValue: string }>) {
        const p = params[0]
        return `${p?.axisValue ?? ''}<br/>回撤: ${p?.value?.toFixed(2) ?? 0}%`
      },
    },
    grid: { left: 50, right: 16, top: 16, bottom: 24 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#6c6c80', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2a4a' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      max: 0,
      splitLine: { lineStyle: { color: '#2a2a4a', type: 'dashed' } },
      axisLabel: {
        color: '#6c6c80',
        fontSize: 10,
        formatter: (v: number) => `${v}%`,
      },
    },
    series: [
      {
        type: 'line',
        data: values,
        showSymbol: false,
        lineStyle: { color: '#ff1744', width: 1.5 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(255, 23, 68, 0.05)' },
              { offset: 1, color: 'rgba(255, 23, 68, 0.35)' },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: {
            color: '#ff1744',
            type: 'dashed',
            width: 1,
          },
          data: [
            {
              yAxis: props.circuitBreakerLevel * 100,
              label: {
                formatter: `熔断线 ${props.circuitBreakerLevel * 100}%`,
                color: '#ff1744',
                fontSize: 10,
                position: 'insideEndTop',
              },
            },
          ],
        },
      },
    ],
  }
})
</script>

<style lang="scss" scoped>
.drawdown-chart {
  width: 100%;
  height: 100%;
}

.chart {
  width: 100%;
  height: 100%;
  min-height: 240px;
}
</style>
