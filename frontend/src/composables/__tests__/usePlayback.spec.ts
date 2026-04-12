import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { usePlayback } from '@/composables/usePlayback'

describe('usePlayback', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('initial currentRound is 1, not playing', () => {
    const total = ref(20)
    const pb = usePlayback(total)
    expect(pb.currentRound.value).toBe(1)
    expect(pb.isPlaying.value).toBe(false)
  })

  it('play advances current round at interval cadence', () => {
    const total = ref(5)
    const pb = usePlayback(total, 400)
    pb.play()
    expect(pb.isPlaying.value).toBe(true)
    vi.advanceTimersByTime(400)
    expect(pb.currentRound.value).toBe(2)
    vi.advanceTimersByTime(400)
    expect(pb.currentRound.value).toBe(3)
  })

  it('pause stops timer and sets isPlaying false', () => {
    const total = ref(20)
    const pb = usePlayback(total, 400)
    pb.play()
    vi.advanceTimersByTime(400)
    pb.pause()
    expect(pb.isPlaying.value).toBe(false)
    const roundAtPause = pb.currentRound.value
    vi.advanceTimersByTime(800)
    expect(pb.currentRound.value).toBe(roundAtPause)
  })

  it('toggle switches between play and pause', () => {
    const total = ref(20)
    const pb = usePlayback(total)
    pb.toggle()
    expect(pb.isPlaying.value).toBe(true)
    pb.toggle()
    expect(pb.isPlaying.value).toBe(false)
  })

  it('step positive delta clamps at total rounds', () => {
    const total = ref(5)
    const pb = usePlayback(total)
    pb.seek(4)
    pb.step(10)
    expect(pb.currentRound.value).toBe(5)
  })

  it('step negative delta clamps at 1', () => {
    const total = ref(5)
    const pb = usePlayback(total)
    pb.seek(2)
    pb.step(-10)
    expect(pb.currentRound.value).toBe(1)
  })

  it('seek sets current round directly and pauses', () => {
    const total = ref(20)
    const pb = usePlayback(total)
    pb.play()
    pb.seek(15)
    expect(pb.currentRound.value).toBe(15)
    expect(pb.isPlaying.value).toBe(false)
  })

  it('reset returns to round 1 and pauses', () => {
    const total = ref(20)
    const pb = usePlayback(total)
    pb.seek(10)
    pb.play()
    pb.reset()
    expect(pb.currentRound.value).toBe(1)
    expect(pb.isPlaying.value).toBe(false)
  })

  it('play after end restarts from round 1', () => {
    const total = ref(3)
    const pb = usePlayback(total, 100)
    pb.seek(3)
    pb.play()
    expect(pb.currentRound.value).toBe(1)
  })

  it('playback stops automatically at final round', () => {
    const total = ref(3)
    const pb = usePlayback(total, 100)
    pb.play()
    vi.advanceTimersByTime(200)
    expect(pb.currentRound.value).toBe(3)
    vi.advanceTimersByTime(100)
    expect(pb.isPlaying.value).toBe(false)
  })
})
