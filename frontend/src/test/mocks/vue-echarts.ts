import { defineComponent, h } from 'vue'

const VChart = defineComponent({
  name: 'VChart',
  props: {
    option: { type: Object, default: null },
    autoresize: { type: [Boolean, Object], default: false },
    group: { type: String, default: '' },
    manualUpdate: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    loadingOptions: { type: Object, default: () => ({}) },
    theme: { type: [String, Object], default: '' },
    initOptions: { type: Object, default: () => ({}) },
    updateOptions: { type: Object, default: () => ({}) },
  },
  emits: [
    'click',
    'dblclick',
    'mousedown',
    'mouseup',
    'mouseover',
    'mouseout',
    'globalout',
    'contextmenu',
    'legendselectchanged',
    'datazoom',
    'restore',
    'highlight',
    'downplay',
  ],
  setup(_, { emit }) {
    return () =>
      h('div', {
        'data-testid': 'vchart-stub',
        onClick: (payload: unknown) => emit('click', payload),
      })
  },
})

export default VChart

export const THEME_KEY = Symbol('ECHARTS_THEME_KEY')
export const LOADING_OPTIONS_KEY = Symbol('ECHARTS_LOADING_OPTIONS_KEY')
export const UPDATE_OPTIONS_KEY = Symbol('ECHARTS_UPDATE_OPTIONS_KEY')
export const INIT_OPTIONS_KEY = Symbol('ECHARTS_INIT_OPTIONS_KEY')
