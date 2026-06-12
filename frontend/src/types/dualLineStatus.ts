/**
 * Z-005 — dual-line daily run-state types.
 *
 * Mirrors ``GET /api/dual-line-status`` (P1-5-amendment-2026-06-01 §1.2
 * 编排). Display-only liveness + bounded caps; polling, no new WS class.
 */

export interface DualLineLine1 {
  readonly label: string
  readonly wired: boolean
  readonly max_debates_per_day: number | null
}

export interface DualLineLine2 {
  readonly label: string
  readonly daily_wired: boolean
  readonly intraday_wired: boolean
}

export interface DualLineRotation {
  readonly label: string
  readonly wired: boolean
  readonly max_total_positions: number | null
}

export interface DualLineStatusPayload {
  readonly line1: DualLineLine1
  readonly line2: DualLineLine2
  readonly rotation: DualLineRotation
  readonly scheduler_wired: boolean
  readonly note: string
}
