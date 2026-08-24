/**
 * P1-5 §1.1 four-group navigation taxonomy contract tests.
 *
 * Locks the 4 group ids, their order, and the absence of legacy /
 * deferred routes (Simulation, AgentDebate, Reconciliation, Execution
 * Report Entry) so subsequent Phase G commits cannot silently regress
 * the locked menu shape.
 */
import { describe, expect, it } from 'vitest'
import { NAV_GROUPS, SETTINGS_ENTRIES } from '@/router/menu'

describe('NAV_GROUPS', () => {
  it('locks the 4-group order: runtime → decisions → ledger → review', () => {
    const ids = NAV_GROUPS.map((g) => g.id)
    expect(ids).toEqual(['runtime', 'decisions', 'ledger', 'review'])
  })

  it('exposes the 4 P1-5 group titles', () => {
    const titles = NAV_GROUPS.map((g) => g.title)
    expect(titles).toEqual([
      '运行状态',
      '决策与指令',
      '账本与成交',
      '复盘与验收',
    ])
  })

  it('omits Simulation from the main menu (P1-5 §2 lock — visualization deferred)', () => {
    const allPaths = NAV_GROUPS.flatMap((g) => g.entries.map((e) => e.path))
    expect(allPaths).not.toContain('/simulation')
  })

  it('exposes AgentDebate in review group (G-008 Phase B 收尾)', () => {
    const allPaths = NAV_GROUPS.flatMap((g) => g.entries.map((e) => e.path))
    expect(allPaths).toContain('/agent-debate')
  })

  it('ledger group contains G-005 + G-006 entries', () => {
    const ledger = NAV_GROUPS.find((g) => g.id === 'ledger')
    expect(ledger?.entries.map((e) => e.path)).toContain('/execution-reports')
    expect(ledger?.entries.map((e) => e.path)).toContain('/reconciliation-center')
  })

  it('runtime group contains both Dashboard and SystemStatus', () => {
    const runtime = NAV_GROUPS.find((g) => g.id === 'runtime')
    expect(runtime?.entries.map((e) => e.path)).toEqual([
      '/dashboard',
      '/system-status',
    ])
  })

  it('decisions group has InstructionPlan pool entry (G-003)', () => {
    const decisions = NAV_GROUPS.find((g) => g.id === 'decisions')
    expect(decisions?.entries.map((e) => e.path)).toContain('/instruction-plans')
  })

  it('ledger group includes Portfolio + account-lines + execution-reports + reconciliation-center', () => {
    const ledger = NAV_GROUPS.find((g) => g.id === 'ledger')
    expect(ledger?.entries.map((e) => e.path)).toEqual([
      '/portfolio',
      '/account-lines',
      '/execution-reports',
      '/reconciliation-center',
    ])
  })

  it('review group contains all 7 entries (3 core + 4 Phase B 收尾)', () => {
    const review = NAV_GROUPS.find((g) => g.id === 'review')
    expect(review?.entries.map((e) => e.path)).toEqual([
      '/performance',
      '/acceptance-reports',
      '/risk-center',
      '/agent-debate',
      '/data-quality',
      '/feishu-messages',
      '/cost-breakdown',
    ])
  })

  it('every menu path starts with a leading slash', () => {
    for (const group of NAV_GROUPS) {
      for (const entry of group.entries) {
        expect(entry.path.startsWith('/')).toBe(true)
      }
    }
  })

  it('every menu entry has a non-empty title', () => {
    for (const group of NAV_GROUPS) {
      for (const entry of group.entries) {
        expect(entry.title.length).toBeGreaterThan(0)
      }
    }
  })
})

describe('SETTINGS_ENTRIES', () => {
  it('exposes the 4 settings sub-pages', () => {
    expect(SETTINGS_ENTRIES.map((e) => e.path)).toEqual([
      '/settings/llm-router',
      '/settings/data-sources',
      '/settings/mirofish',
      '/settings/cost-dashboard',
    ])
  })

  it('every settings path is under /settings/', () => {
    for (const entry of SETTINGS_ENTRIES) {
      expect(entry.path.startsWith('/settings/')).toBe(true)
    }
  })
})
