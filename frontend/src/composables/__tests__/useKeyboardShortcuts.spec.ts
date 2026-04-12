import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import type { Playback } from '@/composables/usePlayback'

function makePlayback(): Playback {
  return {
    currentRound: ref(5),
    isPlaying: ref(false),
    toggle: vi.fn(),
    step: vi.fn(),
    seek: vi.fn(),
    reset: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
  }
}

function pressKey(key: string): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
}

/** Mount a minimal component that calls the composable in its setup context. */
function mountWithShortcuts(
  pb: Playback,
  focusToggle: () => void,
  glyphFlash: () => void,
  totalRounds = ref(20),
) {
  const Comp = defineComponent({
    setup() {
      useKeyboardShortcuts(pb, { totalRounds, focusToggle, glyphFlash })
      return () => h('div')
    },
  })
  return mount(Comp, { attachTo: document.body })
}

describe('useKeyboardShortcuts', () => {
  let pb: Playback
  let focusToggle: ReturnType<typeof vi.fn>
  let glyphFlash: ReturnType<typeof vi.fn>
  let wrapper: ReturnType<typeof mount>

  beforeEach(() => {
    pb = makePlayback()
    focusToggle = vi.fn()
    glyphFlash = vi.fn()
    wrapper = mountWithShortcuts(pb, focusToggle, glyphFlash)
  })

  afterEach(() => {
    wrapper.unmount()
  })

  it('space calls playback toggle', () => {
    pressKey(' ')
    expect(pb.toggle).toHaveBeenCalled()
  })

  it('arrow_right calls step positive 1', () => {
    pressKey('ArrowRight')
    expect(pb.step).toHaveBeenCalledWith(1)
  })

  it('arrow_left calls step negative 1', () => {
    pressKey('ArrowLeft')
    expect(pb.step).toHaveBeenCalledWith(-1)
  })

  it('home calls seek with 1', () => {
    pressKey('Home')
    expect(pb.seek).toHaveBeenCalledWith(1)
  })

  it('end calls seek with totalRounds value', () => {
    pressKey('End')
    expect(pb.seek).toHaveBeenCalledWith(20)
  })

  it('f toggles focus mode', () => {
    pressKey('f')
    expect(focusToggle).toHaveBeenCalled()
  })

  it('r calls reset then play', () => {
    pressKey('r')
    expect(pb.reset).toHaveBeenCalled()
    expect(pb.play).toHaveBeenCalled()
  })

  it('question mark calls glyphFlash', () => {
    pressKey('?')
    expect(glyphFlash).toHaveBeenCalled()
  })

  it('ignores keys when input element is focused', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    pressKey(' ')
    expect(pb.toggle).not.toHaveBeenCalled()

    document.body.removeChild(input)
  })

  it('removes listeners on unmount', () => {
    wrapper.unmount()
    vi.clearAllMocks()
    pressKey(' ')
    expect(pb.toggle).not.toHaveBeenCalled()
    // Re-mount for afterEach cleanup to work cleanly
    wrapper = mountWithShortcuts(makePlayback(), vi.fn(), vi.fn())
  })
})
