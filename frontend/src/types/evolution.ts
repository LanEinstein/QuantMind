/**
 * Front-end mirror of backend ``GET /api/evolution/pending`` payload.
 *
 * Source of truth — backend Pydantic models in
 * ``backend/api/evolution.py``:
 *
 * * ``PendingResponse``  → ``EvolutionPendingPayload``
 * * ``PendingAmendment`` → ``EvolutionPendingItem``
 *
 * The X-023 ``SystemStatus.vue`` card renders one of three colours
 * (green / yellow / red) based on the count + thresholds the backend
 * emits — so the threshold itself ships in the payload (instead of
 * being hard-coded in the front-end) and any future amendment can
 * drift the bands without a front-end change.
 */

export interface EvolutionPendingItem {
  readonly amendment_id: string
  readonly filename: string
  readonly mtime: string
  readonly size_bytes: number
}

export interface EvolutionPendingPayload {
  readonly pending_dir: string
  readonly count: number
  readonly yellow_threshold: number
  readonly red_threshold: number
  readonly items: readonly EvolutionPendingItem[]
  readonly timestamp: string
}

/**
 * Visual status of the X-023 SystemStatus.vue evolution card.
 *
 * * ``green``       — zero pending amendments (the happy case).
 * * ``yellow``      — ``count`` is in ``[yellow_threshold, red_threshold)``;
 *                     the operator has a handful of drafts to review.
 * * ``red``         — ``count`` is at or above ``red_threshold``; the
 *                     review queue is backing up — page the owner.
 * * ``unavailable`` — the polling fetch has failed (network outage,
 *                     backend not wired); the card renders grey.
 */
export type EvolutionPendingStatus = 'green' | 'yellow' | 'red' | 'unavailable'

/**
 * Map ``count`` + threshold pair to the rendered colour band.
 *
 * Pure function so the same logic powers the vue render and the
 * store's getter (and is unit-tested in isolation).
 *
 * @param count - pending amendments count (negative ⇒ treated as 0)
 * @param yellowThreshold - inclusive lower-bound of the yellow band
 * @param redThreshold - inclusive lower-bound of the red band
 */
export function evolutionPendingStatus(
  count: number,
  yellowThreshold: number,
  redThreshold: number,
): Exclude<EvolutionPendingStatus, 'unavailable'> {
  const safeCount = Math.max(0, Math.floor(count))
  if (safeCount >= redThreshold) {
    return 'red'
  }
  if (safeCount >= yellowThreshold) {
    return 'yellow'
  }
  return 'green'
}

// AD-003 — evolution panel: GET /api/evolution/history.

export interface ExperimentSummary {
  readonly experiment_id: string
  readonly kind: string
  readonly family: string
  readonly hypothesis: string
  readonly success: boolean
  readonly trading_days: number
  readonly sample_count: number
  readonly metrics: Readonly<Record<string, number>>
  readonly registered_at: string
}

export interface IntentSummary {
  readonly intent_id: string
  readonly action: string
  readonly kind: string
  readonly family: string
  readonly manifest_hash: string
  readonly status: string
  readonly last_event_at: string
}

export interface CurrentManifest {
  readonly version: string
  readonly updated_at: string | null
  readonly approved: Readonly<Record<string, readonly string[]>>
}

export interface EvolutionHistoryPayload {
  readonly experiments: readonly ExperimentSummary[]
  readonly intents: readonly IntentSummary[]
  readonly current_manifest: CurrentManifest | null
  readonly source: 'mongo' | 'unavailable'
  readonly timestamp: string
}
