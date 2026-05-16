/**
 * Four-group P1-5 navigation taxonomy.
 *
 * Source of truth for the AppShell sidebar. Each Phase G task wires its
 * route here so the menu shape is committed-to in one place.
 *
 * P1-5 §2 redline lock: Simulation.vue stays in the codebase but is
 * intentionally absent from this list (visualization scope deferred to a
 * later phase) — its direct URL still works for ad-hoc visits.
 *
 * G-008 Phase B 收尾 brings 4 follow-on pages into the review group:
 * Agent 辩论, 数据质量, 飞书消息历史, 成本拆解面板.
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
      { path: '/execution-reports', title: '用户回报录入' },
      { path: '/reconciliation-center', title: '对账裁定中心' },
    ],
  },
  {
    id: 'review',
    title: '复盘与验收',
    entries: [
      { path: '/performance', title: '绩效报告' },
      { path: '/acceptance-reports', title: '验收报告' },
      { path: '/risk-center', title: '风控中心' },
      { path: '/agent-debate', title: 'Agent 辩论(只读历史)' },
      { path: '/data-quality', title: '数据质量' },
      { path: '/feishu-messages', title: '飞书消息历史' },
      { path: '/cost-breakdown', title: '成本拆解面板' },
    ],
  },
]

export const SETTINGS_ENTRIES: readonly NavEntry[] = [
  { path: '/settings/llm-router', title: 'LLM 路由' },
  { path: '/settings/data-sources', title: '数据源' },
  { path: '/settings/mirofish', title: 'MiroFish 配置' },
  { path: '/settings/cost-dashboard', title: '成本统计' },
]
