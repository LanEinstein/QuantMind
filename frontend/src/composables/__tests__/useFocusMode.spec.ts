import { describe, it, expect, beforeEach } from 'vitest'
import { useFocusMode } from '@/composables/useFocusMode'

describe('useFocusMode', () => {
  beforeEach(() => {
    document.body.classList.remove('focus-mode')
    localStorage.removeItem('mirofish.focusMode')
  })

  it('toggle adds focus-mode class to body', () => {
    const { active, toggle } = useFocusMode()
    expect(active.value).toBe(false)
    toggle()
    expect(active.value).toBe(true)
    expect(document.body.classList.contains('focus-mode')).toBe(true)
  })

  it('toggle twice removes class', () => {
    const { active, toggle } = useFocusMode()
    toggle()
    toggle()
    expect(active.value).toBe(false)
    expect(document.body.classList.contains('focus-mode')).toBe(false)
  })
})
