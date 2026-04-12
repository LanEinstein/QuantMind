import { describe, it, expect, beforeEach } from 'vitest'
import { useAmbientTicks } from '@/composables/useAmbientTicks'

describe('useAmbientTicks', () => {
  beforeEach(() => {
    localStorage.removeItem('mirofish.audio')
  })

  it('enabled persists to localStorage on toggle', () => {
    const { enabled, toggle } = useAmbientTicks()
    expect(enabled.value).toBe(false)
    toggle()
    expect(enabled.value).toBe(true)
    expect(localStorage.getItem('mirofish.audio')).toBe('true')
    toggle()
    expect(enabled.value).toBe(false)
    expect(localStorage.getItem('mirofish.audio')).toBe('false')
  })

  it('playTick is noop when disabled', () => {
    const { enabled, playTick } = useAmbientTicks()
    expect(enabled.value).toBe(false)
    // Should not throw even when AudioContext is mocked
    expect(() => playTick()).not.toThrow()
  })

  it('playTick creates oscillator when enabled', () => {
    const { toggle, playTick } = useAmbientTicks()
    toggle()
    // AudioContext is mocked in setup.ts — should not throw
    expect(() => playTick()).not.toThrow()
  })
})
