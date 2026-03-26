<template>
  <div class="risk-radar">
    <v-chart :option="chartOption" autoresize class="chart" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent, RadarComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { RiskRadarData } from '@/types/risk'

use([RadarChart, TooltipComponent, RadarComponent, CanvasRenderer])

const props = defineProps<{
  data: RiskRadarData
}>()

const chartOption = computed(() => {
  // Normalize each axis to percentage of its limit
  const axes = [
    {
      name: '总仓位',
      current: props.data.total_position_pct,
      limit: props.data.total_position_limit,
    },
    {
      name: '最大单股',
      current: props.data.max_single_stock_pct,
      limit: props.data.max_single_stock_limit,
    },
    {
      name: '行业集中度',
      current: props.data.industry_concentration_pct,
      limit: props.data.industry_concentration_limit,
    },
    {
      name: '日内亏损',
      current: props.data.daily_loss_pct,
      limit: props.data.daily_loss_limit,
    },
    {
      name: '持股数量',
      current: props.data.stock_count,
      limit: props.data.stock_count_limit,
    },
  ]

  const currentValues = axes.map((a) => {
    // Normalized to 0-100 scale based on limit (guard against zero limit)
    return a.limit !== 0 ? Math.round((a.current / a.limit) * 100) : 0
  })

  const limitValues = axes.map(() => 100)

  // Determine if any axis exceeds limit
  const hasExceeded = currentValues.some((v) => v > 100)

  const indicators = axes.map((a) => ({
    name: `${a.name}\n${a.current}/${a.limit}`,
    max: 120, // Allow some overflow for visual
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 12 },
    },
    radar: {
      indicator: indicators,
      shape: 'polygon',
      axisName: {
        color: '#a0a0b0',
        fontSize: 11,
      },
      splitArea: {
        areaStyle: {
          color: ['transparent'],
        },
      },
      splitLine: {
        lineStyle: { color: '#2a2a4a' },
      },
      axisLine: {
        lineStyle: { color: '#2a2a4a' },
      },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            name: '限制线',
            value: limitValues,
            symbol: 'none',
            lineStyle: {
              color: '#ff1744',
              type: 'dashed',
              width: 1.5,
            },
            areaStyle: { color: 'transparent' },
          },
          {
            name: '当前持仓',
            value: currentValues,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: {
              color: hasExceeded ? '#ff1744' : '#00c853',
              width: 2,
            },
            areaStyle: {
              color: hasExceeded
                ? 'rgba(255, 23, 68, 0.15)'
                : 'rgba(0, 200, 83, 0.15)',
            },
            itemStyle: {
              color: hasExceeded ? '#ff1744' : '#00c853',
            },
          },
        ],
      },
    ],
  }
})
</script>

<style lang="scss" scoped>
.risk-radar {
  width: 100%;
  height: 100%;
}

.chart {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
</style>
