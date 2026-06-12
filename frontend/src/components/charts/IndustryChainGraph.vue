<template>
  <div class="industry-chain-graph">
    <v-chart
      v-if="nodes.length > 0"
      :option="chartOption"
      autoresize
      class="chart"
    />
    <div v-else class="chain-empty">暂无产业链数据</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  CHAIN_NODE_TYPE_LABELS,
  CHAIN_NODE_TYPE_ORDER,
  type IndustryChainEdge,
  type IndustryChainNode,
} from '@/types/themeResearch'

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  nodes: readonly IndustryChainNode[]
  edges: readonly IndustryChainEdge[]
}>()

// One colour per chain layer (趋势→板块→环节→产品→标的).
const LAYER_COLORS: Readonly<Record<string, string>> = {
  Trend: '#a78bfa',
  Sector: '#448aff',
  ChainLink: '#ffb74d',
  Product: '#4dd0e1',
  Instrument: '#ff7597',
}

function typeLabel(nodeType: string): string {
  return CHAIN_NODE_TYPE_LABELS[nodeType] ?? nodeType
}

/** Chain links scale with choke-point composite so the bottleneck stands out. */
function symbolSize(node: IndustryChainNode): number {
  if (node.node_type === 'ChainLink' && node.chokepoint) {
    return 18 + node.chokepoint.composite * 42
  }
  const base: Readonly<Record<string, number>> = {
    Trend: 30,
    Sector: 26,
    Product: 18,
    Instrument: 18,
  }
  return base[node.node_type] ?? 18
}

const categories = computed(() => {
  const present = CHAIN_NODE_TYPE_ORDER.filter((t) =>
    props.nodes.some((n) => n.node_type === t),
  )
  return present.map((t) => ({
    name: typeLabel(t),
    itemStyle: { color: LAYER_COLORS[t] ?? '#8e8ea0' },
  }))
})

const chartOption = computed(() => {
  const categoryIndex = new Map(
    categories.value.map((c, i) => [c.name, i]),
  )
  const data = props.nodes.map((n) => ({
    id: n.node_id,
    name: n.name,
    category: categoryIndex.get(typeLabel(n.node_type)) ?? 0,
    symbolSize: symbolSize(n),
    value: n.chokepoint ? n.chokepoint.composite : 0,
    nodeType: typeLabel(n.node_type),
  }))
  const links = props.edges.map((e) => ({
    source: e.src_id,
    target: e.dst_id,
    label: { show: false },
    value: e.edge_type,
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#16213e',
      borderColor: '#2a2a4a',
      textStyle: { color: '#e0e0e0', fontSize: 12 },
      formatter(p: {
        dataType?: string
        data: { name?: string; nodeType?: string; value?: number | string }
      }) {
        if (p.dataType === 'edge') return String(p.data.value ?? '')
        const composite =
          typeof p.data.value === 'number' ? p.data.value.toFixed(3) : '—'
        return `${p.data.nodeType ?? ''} · ${p.data.name ?? ''}<br/>choke 综合分: ${composite}`
      },
    },
    legend: {
      data: categories.value.map((c) => c.name),
      textStyle: { color: '#a0a0b0', fontSize: 11 },
      top: 0,
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: categories.value,
        force: { repulsion: 240, edgeLength: 95, gravity: 0.08 },
        label: {
          show: true,
          position: 'right',
          color: '#e0e0e0',
          fontSize: 10,
        },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 7,
        lineStyle: { color: '#5a5a7a', curveness: 0.08, opacity: 0.85 },
        emphasis: { focus: 'adjacency' },
        data,
        links,
      },
    ],
  }
})

defineExpose({ chartOption, categories })
</script>

<style lang="scss" scoped>
.industry-chain-graph {
  width: 100%;
  height: 100%;
}
.chart {
  width: 100%;
  height: 100%;
  min-height: 360px;
}
.chain-empty {
  padding: 48px;
  text-align: center;
  color: $text-muted;
  font-size: 13px;
}
</style>
