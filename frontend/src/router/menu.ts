/**
 * Four-group P1-5 navigation taxonomy.
 *
 * Source of truth for the AppShell sidebar. Routes added to plan but not yet
 * implemented (G-002/G-003/G-007 placeholders) are wired here so subsequent
 * Phase G commits extend the same NAV_GROUPS shape rather than re-flattening.
 *
 * P1-5 §2 redline lock: Simulation.vue + AgentDebate.vue stay in the codebase
 * (Simulation visualization deferred; AgentDebate moved to G-008 Phase B
 * 收尾) but are intentionally absent from this list — direct URLs still work
 * for ad-hoc visits.
 */

export interface NavEntry {
  readonly path: string
  readonly title: string
}

export interface NavGroup {
  readonly id: string
  readonly title: string
  readonly entries: readonly NavEntry[]
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    id: 'runtime',
    title: '运行状态',
    entries: [
      { path: '/dashboard', title: '大盘监控' },
      { path: '/system-status', title: '系统状态(5 冻结源)' },
    ],
  },
  {
    id: 'decisions',
    title: '决策与指令',
    entries: [
      { path: '/instruction-plans', title: 'InstructionPlan 池' },
    ],
  },
  {
    id: 'ledger',
    title: '账本与成交',
    entries: [
      { path: '/portfolio', title: '组合(只读)' },
    ],
  },
  {
    id: 'review',
    title: '复盘与验收',
    entries: [
      { path: '/performance', title: '绩效报告' },
      { path: '/acceptance-reports', title: '验收报告' },
      { path: '/risk-center', title: '风控中心' },
    ],
  },
]

export const SETTINGS_ENTRIES: readonly NavEntry[] = [
  { path: '/settings/llm-router', title: 'LLM 路由' },
  { path: '/settings/data-sources', title: '数据源' },
  { path: '/settings/mirofish', title: 'MiroFish 配置' },
  { path: '/settings/cost-dashboard', title: '成本统计' },
]
