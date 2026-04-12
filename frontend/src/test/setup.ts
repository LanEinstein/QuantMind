/** Global test setup for Vitest + jsdom environment. */

// Mock ResizeObserver (not available in jsdom)
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/**
 * Mock HTMLCanvasElement.getContext so ECharts/zrender doesn't throw
 * "Not implemented: HTMLCanvasElement.prototype.getContext" in jsdom.
 * We never render actual charts in tests (VChart is stubbed), but
 * ECharts registers a CanvasRenderer that starts an animation loop —
 * this mock silences its canvas calls without breaking tests.
 */
const mockCtx: Partial<CanvasRenderingContext2D> = {
  clearRect() {},
  fillRect() {},
  strokeRect() {},
  beginPath() {},
  closePath() {},
  moveTo() {},
  lineTo() {},
  arc() {},
  arcTo() {},
  quadraticCurveTo() {},
  bezierCurveTo() {},
  rect() {},
  fill() {},
  stroke() {},
  clip() {},
  save() {},
  restore() {},
  translate() {},
  scale() {},
  rotate() {},
  transform() {},
  setTransform() {},
  resetTransform() {},
  fillText() {},
  strokeText() {},
  measureText: () => ({ width: 0, actualBoundingBoxAscent: 0, actualBoundingBoxDescent: 0 }) as TextMetrics,
  drawImage() {},
  putImageData() {},
  createImageData: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }) as ImageData,
  getImageData: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }) as ImageData,
  setLineDash() {},
  getLineDash: () => [],
  isPointInPath: () => false,
  isPointInStroke: () => false,
  createLinearGradient: () => ({ addColorStop() {} }) as CanvasGradient,
  createRadialGradient: () => ({ addColorStop() {} }) as CanvasGradient,
  createPattern: () => null,
}

Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
  value: () => mockCtx,
  writable: true,
})

// Mock matchMedia (not available in jsdom)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// Mock AudioContext — useAmbientTicks uses this, but jsdom has no audio support
class MockOscillatorNode {
  type = 'sine'
  frequency = { value: 0 }
  connect() { return this }
  start() {}
  stop() {}
  disconnect() {}
}

class MockGainNode {
  gain = { value: 0 }
  connect() { return this }
  disconnect() {}
}

class MockAudioContext {
  currentTime = 0
  destination = {}
  state = 'running'
  createOscillator() { return new MockOscillatorNode() }
  createGain() { return new MockGainNode() }
  close() { return Promise.resolve() }
}

Object.defineProperty(window, 'AudioContext', {
  writable: true,
  value: MockAudioContext,
})
