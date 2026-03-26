<template>
  <div class="sentiment-chart">
    <div class="chart-toolbar">
      <el-button
        :icon="isPlaying ? VideoPause : VideoPlay"
        circle
        size="small"
        @click="togglePlayback"
      />
      <span class="playback-label">
        {{ isPlaying ? '播放中...' : '回放动画' }}
      </span>
      <span class="round-indicator">
        R{{ displayedRound }} / {{ totalRounds }}
      </span>
    </div>
    <div class="chart-body">
      <v-chart :option="chartOption" autoresize />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue'
import { VideoPlay, VideoPause } from '@element-plus/icons-vue'
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

const totalRounds = computed(() => props.sentimentData.length)
const displayedRound = ref(totalRounds.value)
const isPlaying = ref(false)
let playbackTimer: ReturnType<typeof setInterval> | null = null

// Sync displayedRound when data changes and not playing
watch(totalRounds, (val) => {
  if (!isPlaying.value) displayedRound.value = val
})

function togglePlayback() {
  if (isPlaying.value) {
    stopPlayback()
    displayedRound.value = totalRounds.value
    return
  }
  displayedRound.value = 1
  isPlaying.value = true
  playbackTimer = setInterval(() => {
    if (displayedRound.value >= totalRounds.value) {
      stopPlayback()
      return
    }
    displayedRound.value += 1
  }, 400)
}

function stopPlayback() {
  isPlaying.value = false
  if (playbackTimer !== null) {
    clearInterval(playbackTimer)
    playbackTimer = null
  }
}

onUnmounted(() => {
  if (playbackTimer !== null) clearInterval(playbackTimer)
})

const visibleData = computed(() =>
  props.sentimentData.slice(0, displayedRound.value),
)

// Map inflection points to round positions for markLine
const inflectionMarkLines = computed(() => {
  return props.inflectionPoints
    .filter((ip) => ip.day <= displayedRound.value)
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

const chartOption = computed(() => {
  const rounds = visibleData.value.map((s) => `R${s.round}`)
  const bullish = visibleData.value.map((s) => Math.round(s.bullish * 100))
  const bearish = visibleData.value.map((s) => Math.round(s.bearish * 100))
  const neutral = visibleData.value.map((s) => Math.round(s.neutral * 100))

  return {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: 40, bottom: 36 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 11 },
      formatter: (params: Array<{ seriesName: string; value: number; marker: string }>) => {
        const header = params[0] ? `<b>${rounds[params[0].value as unknown as number] ?? ''}</b><br/>` : ''
        const lines = params.map(
          (p: { marker: string; seriesName: string; value: number }) =>
            `${p.marker} ${p.seriesName}: ${p.value}%`,
        )
        return header + lines.join('<br/>')
      },
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
    animationDuration: 300,
    series: [
      {
        name: '看多',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity: 0.6 },
        lineStyle: { color: '#00c853', width: 1.5 },
        itemStyle: { color: '#00c853' },
        smooth: true,
        showSymbol: false,
        data: bullish,
        markLine: {
          silent: true,
          symbol: 'none',
          data: inflectionMarkLines.value,
        },
      },
      {
        name: '看空',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity: 0.6 },
        lineStyle: { color: '#ff1744', width: 1.5 },
        itemStyle: { color: '#ff1744' },
        smooth: true,
        showSymbol: false,
        data: bearish,
      },
      {
        name: '中性',
        type: 'line',
        stack: 'sentiment',
        areaStyle: { opacity: 0.4 },
        lineStyle: { color: '#616161', width: 1.5 },
        itemStyle: { color: '#616161' },
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

.chart-toolbar {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  padding-bottom: $gap-sm;
}

.playback-label {
  font-size: 12px;
  color: $text-secondary;
}

.round-indicator {
  margin-left: auto;
  font-size: 11px;
  font-family: monospace;
  color: $text-muted;
}

.chart-body {
  flex: 1;
  min-height: 260px;
}
</style>
