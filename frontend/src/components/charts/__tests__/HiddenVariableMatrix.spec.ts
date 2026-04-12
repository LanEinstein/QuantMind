import { describe, it, expect } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import HiddenVariableMatrix from '@/components/charts/HiddenVariableMatrix.vue'
import type { HiddenVariable } from '@/types/simulation'

/**
 * Stubs for Element Plus components.
 * We use `global.stubs` so they intercept locally-imported components.
 */
const ElCollapseStub = defineComponent({
  name: 'ElCollapse',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-collapse' }, slots.default?.())
  },
})

const ElCollapseItemStub = defineComponent({
  name: 'ElCollapseItem',
  props: ['name'],
  setup(_, { slots }) {
    return () =>
      h('div', { class: 'el-collapse-item' }, [
        h('div', { class: 'el-collapse-item__header' }, slots.title?.()),
        h('div', { class: 'el-collapse-item__content' }, slots.default?.()),
      ])
  },
})

const globalStubs = {
  ElCollapse: ElCollapseStub,
  ElCollapseItem: ElCollapseItemStub,
  // ElIcon and WarningFilled are used in the disclaimer — stub to avoid resolution errors
  ElIcon: { template: '<span class="el-icon"><slot /></span>' },
  WarningFilled: { template: '<i class="warning-icon" />' },
}

function mountMatrix(variables: readonly HiddenVariable[]) {
  return mount(HiddenVariableMatrix, {
    props: { variables },
    global: { stubs: globalStubs },
  })
}

const makeVar = (overrides: Partial<HiddenVariable>): HiddenVariable => ({
  variable: 'DefaultVar',
  probability: 0.5,
  reasoning: 'default reasoning',
  agent_consensus_ratio: 0.5,
  is_absent_from_original: false,
  ...overrides,
})

describe('HiddenVariableMatrix', () => {
  describe('sorting', () => {
    it('renders one collapse item per variable sorted by probability desc', () => {
      const variables = [
        makeVar({ variable: 'Low', probability: 0.2 }),
        makeVar({ variable: 'High', probability: 0.8 }),
        makeVar({ variable: 'Mid', probability: 0.5 }),
      ]
      const wrapper = mountMatrix(variables)
      const items = wrapper.findAll('.el-collapse-item')
      expect(items).toHaveLength(3)
      expect(items[0].text()).toContain('High')
      expect(items[1].text()).toContain('Mid')
      expect(items[2].text()).toContain('Low')
    })

    it('breaks probability ties by agent_consensus_ratio desc', () => {
      const variables = [
        makeVar({ variable: 'LowConsensus', probability: 0.6, agent_consensus_ratio: 0.2 }),
        makeVar({ variable: 'HighConsensus', probability: 0.6, agent_consensus_ratio: 0.8 }),
      ]
      const wrapper = mountMatrix(variables)
      const items = wrapper.findAll('.el-collapse-item')
      expect(items[0].text()).toContain('HighConsensus')
      expect(items[1].text()).toContain('LowConsensus')
    })
  })

  describe('absent variable dashed border', () => {
    it('adds var-absent class when is_absent_from_original is true', () => {
      const variables = [
        makeVar({ variable: 'AbsentVar', probability: 0.7, is_absent_from_original: true }),
        makeVar({ variable: 'PresentVar', probability: 0.3, is_absent_from_original: false }),
      ]
      const wrapper = mountMatrix(variables)
      const headers = wrapper.findAll('.var-header')
      // First item = AbsentVar (higher probability)
      expect(headers[0].classes()).toContain('var-absent')
      expect(headers[1].classes()).not.toContain('var-absent')
    })

    it('does not add var-absent class when is_absent_from_original is false', () => {
      const variables = [makeVar({ is_absent_from_original: false })]
      const wrapper = mountMatrix(variables)
      expect(wrapper.find('.var-header').classes()).not.toContain('var-absent')
    })
  })

  describe('consensus underlay bar', () => {
    it('bar-fill-consensus width matches agent_consensus_ratio', () => {
      const variables = [makeVar({ agent_consensus_ratio: 0.75 })]
      const wrapper = mountMatrix(variables)
      const bar = wrapper.find('.bar-fill-consensus')
      expect(bar.exists()).toBe(true)
      expect(bar.attributes('style')).toContain('75%')
    })

    it('defaults consensus bar to 0% when ratio is 0', () => {
      const variables = [makeVar({ agent_consensus_ratio: 0 })]
      const wrapper = mountMatrix(variables)
      expect(wrapper.find('.bar-fill-consensus').attributes('style')).toContain('0%')
    })
  })

  describe('probability bar', () => {
    it('bar-fill width is proportional to probability', () => {
      const variables = [makeVar({ probability: 0.6 })]
      const wrapper = mountMatrix(variables)
      expect(wrapper.find('.bar-fill').attributes('style')).toContain('60%')
    })

    it('percentage text reflects probability', () => {
      const variables = [makeVar({ probability: 0.73 })]
      const wrapper = mountMatrix(variables)
      expect(wrapper.text()).toContain('73%')
    })
  })

  describe('expanded content', () => {
    it('shows reasoning text', () => {
      const variables = [makeVar({ reasoning: '深度价值分析支撑隐变量' })]
      const wrapper = mountMatrix(variables)
      expect(wrapper.text()).toContain('深度价值分析支撑隐变量')
    })

    it('shows disclaimer text', () => {
      const variables = [makeVar({})]
      const wrapper = mountMatrix(variables)
      expect(wrapper.text()).toContain('群体仿真估计')
    })
  })
})
