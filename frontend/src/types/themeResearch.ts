/**
 * Z-002 — industry-chain reverse-deduction viz types.
 *
 * Mirrors the read-only payload from ``GET /api/theme-research/industry-chain``
 * (P1-5-amendment-2026-06-01 §1.2 direction①). Display-only: the quant path is
 * the qualification authority; this chain never filters universe or vetoes a
 * sector.
 */

export interface ChokePointScore {
  readonly downstream_reach: number
  readonly out_degree: number
  readonly betweenness: number
  readonly pagerank: number
  readonly composite: number
}

export interface IndustryChainNode {
  readonly node_id: string
  readonly node_type: string
  readonly name: string
  readonly attrs: Record<string, unknown>
  readonly chokepoint: ChokePointScore | null
}

export interface IndustryChainEdge {
  readonly edge_id: string
  readonly edge_type: string
  readonly src_id: string
  readonly dst_id: string
}

export interface ChokePointRankEntry extends ChokePointScore {
  readonly node_id: string
  readonly name: string
}

export interface ThemePeerSourcing {
  readonly pinned_candidate_count: number
  readonly note: string
}

export interface IndustryChainPayload {
  readonly available: boolean
  readonly note: string
  readonly node_count: number
  readonly edge_count: number
  readonly chain_link_count: number
  readonly nodes: readonly IndustryChainNode[]
  readonly edges: readonly IndustryChainEdge[]
  readonly chokepoints: readonly ChokePointRankEntry[]
  readonly theme_peer_sourcing: ThemePeerSourcing
}

/** Canonical 趋势→板块→环节→产品→标的 order + zh labels for the viz. */
export const CHAIN_NODE_TYPE_LABELS: Readonly<Record<string, string>> = {
  Trend: '趋势',
  Sector: '板块',
  ChainLink: '产业链环节',
  Product: '产品',
  Instrument: '标的',
}

export const CHAIN_NODE_TYPE_ORDER: readonly string[] = [
  'Trend',
  'Sector',
  'ChainLink',
  'Product',
  'Instrument',
]
