import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import VChart from 'vue-echarts'
import IndustryChainGraph from '@/components/charts/IndustryChainGraph.vue'
import type {
  IndustryChainEdge,
  IndustryChainNode,
} from '@/types/themeResearch'

function node(
  id: string,
  type: string,
  name: string,
  composite: number | null = null,
): IndustryChainNode {
  return {
    node_id: id,
    node_type: type,
    name,
    attrs: {},
    chokepoint:
      composite === null
        ? null
        : {
            downstream_reach: composite,
            out_degree: 0,
            betweenness: 0,
            pagerank: 0,
            composite,
          },
  }
}

function edge(id: string, type: string, src: string, dst: string): IndustryChainEdge {
  return { edge_id: id, edge_type: type, src_id: src, dst_id: dst }
}

const NODES: IndustryChainNode[] = [
  node('trend:semi', 'Trend', '半导体国产替代'),
  node('sector:semi', 'Sector', '半导体'),
  node('link:litho', 'ChainLink', '光刻', 0.2),
  node('link:photoresist', 'ChainLink', '光刻胶', 0.9),
  node('inst:1', 'Instrument', '北方华创'),
]
const EDGES: IndustryChainEdge[] = [
  edge('e1', 'DRIVES', 'trend:semi', 'sector:semi'),
  edge('e2', 'REQUIRES', 'sector:semi', 'link:litho'),
  edge('e3', 'UPSTREAM_OF', 'link:photoresist', 'link:litho'),
]

function optionOf(nodes: IndustryChainNode[], edges: IndustryChainEdge[]) {
  const wrapper = mount(IndustryChainGraph, { props: { nodes, edges } })
  return wrapper.getComponent(VChart).props('option') as Record<string, unknown>
}

describe('IndustryChainGraph', () => {
  it('renders empty state when no nodes', () => {
    const wrapper = mount(IndustryChainGraph, { props: { nodes: [], edges: [] } })
    expect(wrapper.find('.chain-empty').exists()).toBe(true)
    expect(wrapper.findComponent(VChart).exists()).toBe(false)
  })

  it('renders the chart when nodes are present', () => {
    const wrapper = mount(IndustryChainGraph, {
      props: { nodes: NODES, edges: EDGES },
    })
    expect(wrapper.findComponent(VChart).exists()).toBe(true)
    expect(wrapper.find('.chain-empty').exists()).toBe(false)
  })

  it('maps every node and edge into the graph series', () => {
    const option = optionOf(NODES, EDGES)
    const series = (option.series as Array<Record<string, unknown>>)[0]
    expect((series.data as unknown[]).length).toBe(5)
    expect((series.links as unknown[]).length).toBe(3)
    expect(series.type).toBe('graph')
  })

  it('scales a high-composite choke point larger than a weak one', () => {
    const option = optionOf(NODES, EDGES)
    const data = (option.series as Array<{ data: Array<{ id: string; symbolSize: number }> }>)[0]
      .data
    const strong = data.find((d) => d.id === 'link:photoresist')!
    const weak = data.find((d) => d.id === 'link:litho')!
    expect(strong.symbolSize).toBeGreaterThan(weak.symbolSize)
  })

  it('builds legend categories in canonical chain order, present-only', () => {
    const option = optionOf(NODES, EDGES)
    // Product absent in NODES -> excluded; order is 趋势→板块→环节→标的.
    expect(option.legend).toMatchObject({
      data: ['趋势', '板块', '产业链环节', '标的'],
    })
  })
})
