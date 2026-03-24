<template>
  <div class="capital-flow">
    <div class="flow-header">
      <span class="flow-label">北向资金今日净流入</span>
      <span :class="['flow-value', netInflow >= 0 ? 'positive' : 'negative']">
        {{ formatBillion(netInflow) }}亿
      </span>
    </div>
    <v-chart :option="chartOption" autoresize class="flow-chart" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  netInflow: number // in CNY
}>(), {
  netInflow: 0,
})

function formatBillion(value: number): string {
  return (value / 1e8).toFixed(2)
}

// Mock intraday capital flow curve (09:30-11:30 + 13:00-15:00)
const intradayFlow = computed(() => {
  const points: { time: string; value: number }[] = []
  let acc = 0
  for (let m = 0; m < 330; m++) {
    const hour = 9 + Math.floor((m + 30) / 60)
    const min = (m + 30) % 60
    if (hour >= 15) break
    if (hour === 11 && min >= 30) continue
    if (hour === 12) continue
    acc += (Math.random() - 0.47) * 2e7
    points.push({
      time: `${hour.toString().padStart(2, '0')}:${min.toString().padStart(2, '0')}`,
      value: acc,
    })
  }
  // Scale to match net inflow
  if (points.length > 0 && points[points.length - 1].value !== 0) {
    const scale = props.netInflow / points[points.length - 1].value
    for (const p of points) p.value *= scale
  }
  return points
})

const chartOption = computed(() => ({
  backgroundColor: 'transparent',
  grid: { left: 50, right: 8, top: 8, bottom: 24 },
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#16213e',
    borderColor: '#2a2a4a',
    textStyle: { color: '#e0e0e0', fontSize: 11 },
    formatter: (params: { value: number; axisValue: string }[]) => {
      const p = params[0]
      return `${p.axisValue}<br/>净流入: ${formatBillion(p.value)}亿`
    },
  },
  xAxis: {
    type: 'category',
    data: intradayFlow.value.map((d) => d.time),
    axisLabel: { color: '#6c6c80', fontSize: 10, interval: 59 },
    axisLine: { lineStyle: { color: '#2a2a4a' } },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: '#2a2a4a', type: 'dashed' } },
    axisLabel: {
      color: '#6c6c80',
      fontSize: 10,
      formatter: (v: number) => `${(v / 1e8).toFixed(1)}亿`,
    },
  },
  series: [
    {
      type: 'line',
      data: intradayFlow.value.map((d) => d.value),
      showSymbol: false,
      lineStyle: { color: '#448aff', width: 1.5 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: '#448aff30' },
            { offset: 1, color: '#448aff05' },
          ],
        },
      },
    },
  ],
}))
</script>

<style lang="scss" scoped>
.capital-flow {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.flow-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 4px;
}

.flow-label {
  font-size: 13px;
  color: $text-secondary;
}

.flow-value {
  font-size: 20px;
  font-weight: 700;
  font-family: 'Roboto Mono', monospace;

  &.positive { color: $color-up; }
  &.negative { color: $color-down; }
}

.flow-chart {
  flex: 1;
  min-height: 140px;
}
</style>
