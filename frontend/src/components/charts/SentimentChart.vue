<template>
  <div class="sentiment-chart">
    <div class="chart-body">
      <v-chart :option="chartOption" autoresize />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { SentimentSnapshot, InflectionPoint } from '@/types/simulation'
import { PLAYBACK_KEY } from '@/composables/usePlayback'

use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
])

const props = defineProps<{
  sentimentData: readonly SentimentSnapshot[]
  inflectionPoints: readonly InflectionPoint[]
}>()

// Inject global playback clock; fall back to showing all data
const playback = inject(PLAYBACK_KEY)
const currentRound = computed(() => playback?.currentRound.value ?? props.sentimentData.length)

const visibleData = computed(() =>
  props.sentimentData.slice(0, currentRound.value),
)

// Map inflection points to round positions for markLine
const inflectionMarkLines = computed(() => {
  return props.inflectionPoints
    .filter((ip) => ip.day <= currentRound.value)
    .map((ip) => ({
      xAxis: `R${ip.day}`,
      label: {
        formatter: ip.event.slice(0, 8) + '...',
        position: 'insideStartTop' as const,
        fontSize: 10,
        color: '#ffd600',
      },
      lineStyle: {
        type: 'dashed' as const,
        color: '#ffd600',
        width: 1,
      },
    }))
})

// "Now" line at the current round
const nowMarkLine = computed(() => {
  if (visibleData.value.length === 0) return []
  return [{
    xAxis: `R${currentRound.value}`,
    lineStyle: { type: 'solid' as const, color: '#448aff', width: 1.5, opacity: 0.8 },
    label: {
      formatter: `R${currentRound.value}`,
      position: 'insideEndBottom' as const,
      fontSize: 10,
      color: '#448aff',
    },
  }]
})

// Per-round intensity drives area opacity (0.3–0.9)
function intensityToOpacity(intensity: number | undefined): number {
  const i = intensity ?? 0.5
  return 0.3 + i * 0.6
}

const chartOption = computed(() => {
  const rounds = visibleData.value.map((s) => `R${s.round}`)
  const bullish = visibleData.value.map((s) => Math.round(s.bullish * 100))
  const bearish = visibleData.value.map((s) => Math.round(s.bearish * 100))
  const neutral = visibleData.value.map((s) => Math.round(s.neutral * 100))
  // Average intensity for area opacity (simplified: use last snapshot's value)
  const lastSnap = visibleData.value[visibleData.value.length - 1]
  const opacity = intensityToOpacity(lastSnap?.intensity)

  const markLineData = [...inflectionMarkLines.value, ...nowMarkLine.value]

  return {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: 40, bottom: 36 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 11 },
    },
    legend: {
      top: 4,
      right: 8,
      textStyle: { color: '#a0a0b0', fontSize: 11 },
      data: ['看多', '看空', '中性'],
    },
    xAxis: {
      type: 'category',
      data: rounds,
      axisLabel: { color: '#6c6c80', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2a4a' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: {
        color: '#6c6c80',
        fontSize: 10,
        formatter: '{value}%',
      },
      splitLine: { lineStyle: { color: '#2a2a4a', type: 'dashed' } },
    },
    animationDuration: 200,
    series: [
      {
        name: '看多',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity },
        lineStyle: { color: '#ff1744', width: 1.5 },
        itemStyle: { color: '#ff1744' },
        smooth: true,
        showSymbol: false,
        data: bullish,
        markLine: {
          silent: true,
          symbol: 'none',
          data: markLineData,
        },
      },
      {
        name: '看空',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity },
        lineStyle: { color: '#00c853', width: 1.5 },
        itemStyle: { color: '#00c853' },
        smooth: true,
        showSymbol: false,
        data: bearish,
      },
      {
        name: '中性',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity: opacity * 0.6 },
        lineStyle: { color: '#ffd600', width: 1.5 },
        itemStyle: { color: '#ffd600' },
        smooth: true,
        showSymbol: false,
        data: neutral,
      },
    ],
  }
})
</script>

<style scoped lang="scss">
.sentiment-chart {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chart-body {
  flex: 1;
  min-height: 260px;
}
</style>
