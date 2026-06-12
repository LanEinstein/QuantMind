/**
 * AD-004 — shared style-badge mapping (短线⚡ / 价值🏛).
 *
 * Single front-end source of truth for the AC StyleClassifier badge, kept in
 * lock-step with the Feishu renderer (`backend/integrations/feishu/renderer.py`
 * `_STYLE_BADGES`, AC-007). Display-only: the style label never changes a
 * number, it only annotates a row/card so the owner can tell "赚快钱" (short
 * term) from "并肩成长" (value) at a glance.
 *
 * `null` / unknown → empty (legacy positions + the pure-quant path render
 * with no badge rather than a misleading one).
 */

export type StyleTag = 'short_term' | 'value'

export interface StyleBadge {
  readonly label: string
  readonly icon: string
  /** Element-Plus tag type for colour. */
  readonly tagType: 'warning' | 'success'
}

const STYLE_BADGES: Readonly<Record<string, StyleBadge>> = {
  short_term: { label: '短线', icon: '⚡', tagType: 'warning' },
  value: { label: '价值', icon: '🏛', tagType: 'success' },
}

/** Resolve a style badge, or `null` when the style is absent/unknown. */
export function styleBadge(style: string | null | undefined): StyleBadge | null {
  if (!style) return null
  return STYLE_BADGES[style] ?? null
}
