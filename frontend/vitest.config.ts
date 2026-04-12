import { defineConfig, mergeConfig } from 'vitest/config'
import type { Plugin } from 'vite'
import { resolve } from 'path'
import viteConfig from './vite.config'

/**
 * Inline Vite plugin that replaces CSS imports with empty modules.
 * This prevents "Unknown file extension .css" errors in the jsdom environment
 * when element-plus (and similar packages) import their theme CSS at runtime.
 */
const stubCssPlugin: Plugin = {
  name: 'stub-css-imports',
  enforce: 'pre',
  resolveId(id) {
    // Guard: never re-wrap virtual modules (ids starting with \0)
    if (id.startsWith('\0')) return
    // Strip query params (e.g., Component.vue?type=style&lang.css) before checking extension
    const cleanPath = id.split('?')[0]
    if (cleanPath.endsWith('.css') || cleanPath.endsWith('.less')) {
      return `\0stub-css:${id}`
    }
  },
  load(id) {
    if (id.startsWith('\0stub-css:')) {
      return 'export default {}'
    }
  },
}

export default mergeConfig(
  viteConfig,
  defineConfig({
    plugins: [stubCssPlugin],
    resolve: {
      alias: {
        'vue-echarts': resolve(__dirname, 'src/test/mocks/vue-echarts.ts'),
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.spec.ts'],
      server: {
        deps: {
          // Process these through Vite so the stubCssPlugin can intercept their CSS imports
          inline: [/element-plus/, /@element-plus/],
        },
      },
      coverage: {
        provider: 'v8',
        include: [
          'src/components/charts/SentimentChart.vue',
          'src/components/charts/HiddenVariableMatrix.vue',
          'src/components/charts/InflectionTimeline.vue',
          'src/components/charts/ExtremeScenarioPie.vue',
          'src/components/charts/MiniSentimentDonut.vue',
          'src/composables/usePlayback.ts',
          'src/composables/useKeyboardShortcuts.ts',
          'src/composables/useFocusMode.ts',
          'src/composables/useAmbientTicks.ts',
          'src/stores/transformers/simulation.ts',
        ],
        thresholds: {
          lines: 70,
          branches: 70,
          functions: 70,
          statements: 70,
        },
      },
    },
  }),
)
