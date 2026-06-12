import { describe, it, expect } from 'vitest'
import { styleBadge } from '@/utils/styleBadge'

describe('styleBadge (AD-004)', () => {
  it('maps short_term to ⚡短线 (warning)', () => {
    const b = styleBadge('short_term')
    expect(b).not.toBeNull()
    expect(b!.icon).toBe('⚡')
    expect(b!.label).toBe('短线')
    expect(b!.tagType).toBe('warning')
  })

  it('maps value to 🏛价值 (success)', () => {
    const b = styleBadge('value')
    expect(b!.icon).toBe('🏛')
    expect(b!.label).toBe('价值')
    expect(b!.tagType).toBe('success')
  })

  it('returns null for null / undefined / unknown', () => {
    expect(styleBadge(null)).toBeNull()
    expect(styleBadge(undefined)).toBeNull()
    expect(styleBadge('')).toBeNull()
    expect(styleBadge('mystery')).toBeNull()
  })
})
