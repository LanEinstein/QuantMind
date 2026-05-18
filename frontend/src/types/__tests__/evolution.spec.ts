/**
 * X-023 — pure ``evolutionPendingStatus`` colour-band tests.
 *
 * The helper powers both the SystemStatus.vue render and the
 * evolution-pinia store getter; unit-testing it in isolation keeps
 * the boundary logic honest regardless of whichever consumer comes
 * next (cli, ws push, etc.).
 */
import { describe, expect, it } from 'vitest'

import { evolutionPendingStatus } from '@/types/evolution'

describe('evolutionPendingStatus', () => {
  it('returns green for count=0', () => {
    expect(evolutionPendingStatus(0, 1, 4)).toBe('green')
  })

  it('returns green when count is negative (defensive clamp)', () => {
    expect(evolutionPendingStatus(-1, 1, 4)).toBe('green')
  })

  it('returns yellow for count at the yellow_threshold lower bound', () => {
    expect(evolutionPendingStatus(1, 1, 4)).toBe('yellow')
  })

  it('returns yellow within the yellow band (count<red_threshold)', () => {
    expect(evolutionPendingStatus(2, 1, 4)).toBe('yellow')
    expect(evolutionPendingStatus(3, 1, 4)).toBe('yellow')
  })

  it('returns red exactly at the red_threshold', () => {
    expect(evolutionPendingStatus(4, 1, 4)).toBe('red')
  })

  it('returns red above the red_threshold', () => {
    expect(evolutionPendingStatus(10, 1, 4)).toBe('red')
  })

  it('truncates a fractional count (defensive Math.floor)', () => {
    expect(evolutionPendingStatus(3.9, 1, 4)).toBe('yellow')
    expect(evolutionPendingStatus(4.5, 1, 4)).toBe('red')
  })

  it('honours bespoke threshold pairs (e.g. yellow=2, red=5)', () => {
    expect(evolutionPendingStatus(1, 2, 5)).toBe('green')
    expect(evolutionPendingStatus(2, 2, 5)).toBe('yellow')
    expect(evolutionPendingStatus(4, 2, 5)).toBe('yellow')
    expect(evolutionPendingStatus(5, 2, 5)).toBe('red')
  })

  it('collapses to red when the bands overlap (red_threshold <= yellow)', () => {
    // Defensive: amendment that sets yellow=red=2 ⇒ count>=2 must
    // surface as red (red branch wins because we check red first).
    expect(evolutionPendingStatus(2, 2, 2)).toBe('red')
    expect(evolutionPendingStatus(1, 2, 2)).toBe('green')
  })
})
