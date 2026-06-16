<template>
  <v-chart :option="chartOption" autoresize class="heatmap-chart" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { TreemapChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { SectorQuote } from '@/types/market'

use([TreemapChart, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  sectors: SectorQuote[]
}>()

const emit = defineEmits<{
  sectorClick: [sector: SectorQuote]
}>()

function sectorColor(pct: number): string {
  if (pct > 3) return '#d50000'
  if (pct > 1.5) return '#ff1744'
  if (pct > 0) return '#ff5252'
  if (pct === 0) return '#424242'
  if (pct > -1.5) return '#69f0ae'
  if (pct > -3) return '#00c853'
  return '#00a844'
}

const chartOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    backgroundColor: '#16213e',
    borderColor: '#2a2a4a',
    textStyle: { color: '#e0e0e0', fontSize: 12 },
    formatter: (params: { data: { name: string; changePct: number; leader: string } }) => {
      const d = params.data
      const pct = Number(d.changePct ?? 0)
      const sign = pct >= 0 ? '+' : ''
      return `<b>${d.name}</b><br/>涨跌幅: ${sign}${pct.toFixed(2)}%<br/>龙头: ${d.leader}`
    },
  },
  series: [
    {
      type: 'treemap',
      width: '100%',
      height: '100%',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: {
        show: true,
        color: '#fff',
        fontSize: 12,
        formatter: (params: { data: { name: string; changePct: number } }) => {
          const pct = Number(params.data.changePct ?? 0)
          const sign = pct >= 0 ? '+' : ''
          return `${params.data.name}\n${sign}${pct.toFixed(2)}%`
        },
      },
      itemStyle: {
        borderColor: '#1a1a2e',
        borderWidth: 2,
        gapWidth: 2,
      },
      data: props.sectors.map((s) => ({
        name: s.name,
        value: Math.abs(s.change_pct) + 1,
        changePct: s.change_pct,
        leader: s.leader_name,
        itemStyle: { color: sectorColor(s.change_pct) },
      })),
    },
  ],
}))
</script>

<style lang="scss" scoped>
.heatmap-chart {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
