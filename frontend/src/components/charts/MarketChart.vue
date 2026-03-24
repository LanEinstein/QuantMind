<template>
  <div class="market-chart">
    <div class="chart-header">
      <span class="index-name">{{ name }}</span>
      <span class="index-price">{{ price.toFixed(2) }}</span>
      <span :class="['index-change', changePct >= 0 ? 'up' : 'down']">
        {{ changePct >= 0 ? '+' : '' }}{{ changePct.toFixed(2) }}%
      </span>
    </div>
    <v-chart :option="chartOption" autoresize class="chart-body" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  name: string
  price: number
  changePct: number
  prevClose?: number
}>(), {
  prevClose: 0,
})

// Generate mock intraday time-series (9:30-11:30 + 13:00-15:00 = 240 trading mins)
const intradayData = computed(() => {
  const points: { time: string; price: number; volume: number }[] = []
  const base = props.prevClose || props.price * (1 - props.changePct / 100)
  let p = base
  // Iterate over 330 wall-clock minutes (09:30 to 15:00), skip lunch 11:30-13:00
  for (let m = 0; m < 330; m++) {
    const hour = 9 + Math.floor((m + 30) / 60)
    const min = (m + 30) % 60
    if (hour >= 15) break
    if (hour === 11 && min >= 30) continue
    if (hour === 12) continue
    p += (Math.random() - 0.48) * base * 0.001
    points.push({
      time: `${hour.toString().padStart(2, '0')}:${min.toString().padStart(2, '0')}`,
      price: +p.toFixed(2),
      volume: Math.floor(Math.random() * 5e8),
    })
  }
  // Ensure last point matches current price
  if (points.length > 0) {
    points[points.length - 1].price = props.price
  }
  return points
})

const lineColor = computed(() => (props.changePct >= 0 ? '#ff1744' : '#00c853'))

const chartOption = computed(() => ({
  backgroundColor: 'transparent',
  grid: [
    { left: 48, right: 8, top: 8, height: '60%' },
    { left: 48, right: 8, top: '75%', height: '20%' },
  ],
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#16213e',
    borderColor: '#2a2a4a',
    textStyle: { color: '#e0e0e0', fontSize: 11 },
  },
  xAxis: [
    {
      type: 'category',
      data: intradayData.value.map((d) => d.time),
      gridIndex: 0,
      axisLabel: { show: false },
      axisLine: { lineStyle: { color: '#2a2a4a' } },
      axisTick: { show: false },
    },
    {
      type: 'category',
      data: intradayData.value.map((d) => d.time),
      gridIndex: 1,
      axisLabel: { color: '#6c6c80', fontSize: 10, interval: 59 },
      axisLine: { lineStyle: { color: '#2a2a4a' } },
      axisTick: { show: false },
    },
  ],
  yAxis: [
    {
      type: 'value',
      gridIndex: 0,
      splitLine: { lineStyle: { color: '#2a2a4a', type: 'dashed' } },
      axisLabel: { color: '#6c6c80', fontSize: 10 },
    },
    {
      type: 'value',
      gridIndex: 1,
      splitLine: { show: false },
      axisLabel: { show: false },
    },
  ],
  series: [
    {
      type: 'line',
      data: intradayData.value.map((d) => d.price),
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { color: lineColor.value, width: 1.5 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: lineColor.value + '40' },
            { offset: 1, color: lineColor.value + '05' },
          ],
        },
      },
    },
    {
      type: 'bar',
      data: intradayData.value.map((d) => d.volume),
      xAxisIndex: 1,
      yAxisIndex: 1,
      itemStyle: { color: '#448aff44' },
    },
  ],
}))
</script>

<style lang="scss" scoped>
.market-chart {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chart-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
}

.index-name {
  font-size: 14px;
  color: $text-primary;
  font-weight: 600;
}

.index-price {
  font-size: 18px;
  font-weight: 700;
  color: $text-primary;
  font-family: 'Roboto Mono', monospace;
}

.index-change {
  font-size: 14px;
  font-weight: 600;
  font-family: 'Roboto Mono', monospace;

  &.up { color: $color-up; }
  &.down { color: $color-down; }
}

.chart-body {
  flex: 1;
  min-height: 160px;
}
</style>
