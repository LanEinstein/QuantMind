/**
 * Phase AD Playwright front-end exam (owner 2026-06-12 gate).
 *
 * Drives a real chromium against `vite preview` (the built dist) and exams
 * the new AD panels across: functional render, console errors, layout
 * overflow, zero-size nodes, and the feishu-gated manual-trade form. ALL
 * /api responses are mocked with realistic data via route interception so
 * the exam is deterministic and needs no backend. Screenshots →
 * /tmp/ad_exam/, JSON report → stdout.
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

const BASE = process.env.EXAM_BASE || 'http://127.0.0.1:9300'
const OUT = '/tmp/ad_exam'
mkdirSync(OUT, { recursive: true })

const ok = (data) => ({ status: 'ok', data, error: null })

// ---- realistic mock payloads for the AD endpoints ----
const equitySeries = Array.from({ length: 50 }, (_, i) => ({
  trade_date: `2026-0${4 + Math.floor((i + 1) / 22)}-${String(((i % 21) + 1)).padStart(2, '0')}`,
  total_equity: 1_000_000 + i * 2400 + (i % 5) * 1500,
  pnl_pct: (i * 2400) / 1_000_000,
  policy_hash: i < 30 ? 'a'.repeat(64) : 'b'.repeat(64),
  quality: i % 9 === 0 ? 'STALE' : 'FRESH',
}))

const MOCKS = {
  '/api/risk/status': ok({
    system_status: 'normal',
    run_mode: { simulation_auto: true, feishu_interactive: true },
    stop_loss_triggers_today: 0,
    circuit_breaker_triggered: false,
    llm_intercepts_today: 2,
  }),
  '/api/performance/equity-kpis': ok({
    kpis: {
      total_return: 0.1284, annualized_return: 0.3160, annualized_reliable: true,
      max_drawdown: -0.0512, sharpe_ratio: 1.42, hs300_excess: 0.061,
      sample_trading_days: 50, policy_segment_count: 2,
      data_quality: { FRESH: 44, STALE: 6 }, latest_total_equity: 1_124_300,
    },
    equity_series: equitySeries,
    policy_segments: [
      { policy_hash: 'a'.repeat(64), started_at: '2026-04-01T00:00:00+00:00', trade_date: '2026-04-01' },
      { policy_hash: 'b'.repeat(64), started_at: '2026-05-20T00:00:00+00:00', trade_date: '2026-05-20' },
    ],
    active_policy_hash: 'b'.repeat(64),
    repository_status: 'ok',
  }),
  '/api/acceptance/latest': ok({
    report: {
      report_id: 'r-1', computed_at: '2026-06-12T08:00:30+00:00', trade_date: '2026-06-12',
      window_start: '2026-04-28', window_end: '2026-06-12', trading_days_in_window: 45,
      outcome: 'PASS',
      metrics: [
        { name: 'instruction_completion_rate', value: 0.97, threshold: 0.95, direction: 'at_least', passed: true },
        { name: 'execution_report_accuracy_rate', value: 0.995, threshold: 0.99, direction: 'at_least', passed: true },
        { name: 'data_missing_rate', value: 0.004, threshold: 0.01, direction: 'at_most', passed: true },
        { name: 'llm_timeout_rate', value: 0.02, threshold: 0.05, direction: 'at_most', passed: true },
        { name: 'signal_generation_rate', value: 0.96, threshold: 0.95, direction: 'at_least', passed: true },
        { name: 'max_drawdown_pct', value: 0.051, threshold: 0.08, direction: 'at_most', passed: true },
        { name: 'pnl_cny', value: 124300, threshold: 0, direction: 'at_least', passed: true },
        { name: 'csi300_excess_pct', value: 0.061, threshold: 0, direction: 'at_least', passed: true },
      ],
      notes: '',
    },
    can_switch_to_feishu_on: true, service_status: 'ok',
  }),
  '/api/evolution/history': ok({
    experiments: [
      { experiment_id: 'e1abc', kind: 'THRESHOLD_PARAM', family: 'sell_stack', hypothesis: '收紧熊市回撤阈值', success: true, trading_days: 45, sample_count: 30, metrics: { sharpe: 1.5 }, registered_at: '2026-06-10T00:00:00+00:00' },
      { experiment_id: 'e2def', kind: 'PROMPT', family: 'theme_sop', hypothesis: '主题倒推措辞 v2', success: false, trading_days: 20, sample_count: 12, metrics: {}, registered_at: '2026-06-08T00:00:00+00:00' },
    ],
    intents: [
      { intent_id: '11111111-1111-1111-1111-111111111111', action: 'PROMOTE', kind: 'THRESHOLD_PARAM', family: 'sell_stack', manifest_hash: 'c'.repeat(64), status: 'ACTIVATED', last_event_at: '2026-06-11T02:00:00+00:00' },
      { intent_id: '22222222-2222-2222-2222-222222222222', action: 'DEMOTE', kind: 'PROMPT', family: 'theme_sop', manifest_hash: 'd'.repeat(64), status: 'ROLLED_BACK', last_event_at: '2026-06-09T02:00:00+00:00' },
    ],
    current_manifest: { version: '1.0', updated_at: '2026-06-11T00:00:00+00:00', approved: { prompt_version: ['a40927ee4b7dc58a857b0c92ea4bada4a3a53af5f143686c6a9c81a71d114bf7'], strategy_code: [], feature_def: [], anomaly_model: [], rag_index: [] } },
    source: 'mongo', timestamp: '2026-06-12T08:00:00+00:00',
  }),
  '/api/instruction-plans': ok({
    plans: [
      { instruction_id: 'QM-20260612-093500-600519-BUY-001', trade_date: '2026-06-12', stock_code: '600519', stock_name: '贵州茅台', side: 'BUY', status: 'FILLED', volume: 100, limit_price: 1700, valid_until: '2026-06-12T09:40:00+00:00', created_at: '2026-06-12T09:35:00+00:00', rejection_reason: null },
      { instruction_id: 'QM-20260612-093600-159949-BUY-001', trade_date: '2026-06-12', stock_code: '159949', stock_name: '创业板50ETF', side: 'BUY', status: 'VALIDATED', volume: 200, limit_price: 1.2, valid_until: '2026-06-12T09:41:00+00:00', created_at: '2026-06-12T09:36:00+00:00', rejection_reason: null },
      { instruction_id: 'QM-20260612-093700-000001-SELL-001', trade_date: '2026-06-12', stock_code: '000001', stock_name: '平安银行', side: 'SELL', status: 'REJECTED', volume: 300, limit_price: 11.5, valid_until: '2026-06-12T09:42:00+00:00', created_at: '2026-06-12T09:37:00+00:00', rejection_reason: 'limit_down_block' },
    ],
    total: 3, repository_status: 'ok',
  }),
  '/api/position-theses': ok({
    available: true, note: '', thesis_count: 1,
    theses: [{ stock_code: '600519', stock_name: '贵州茅台', instruction_id: 'QM-20260612-093500-600519-BUY-001', trade_date: '2026-06-12', created_at: '2026-06-12T09:35:00+00:00', entry_price: 1700, entry_score: 0.82, time_stop_trade_days: 20, catalyst_window_end: null, pillars: ['龙头护城河', '盈利稳健'], invalidation_conditions: [{ template: 'anchor_drawdown', metric_name: 'price', comparator: 'lt', threshold: 1530, anchor: 1700 }], evidence_ids: ['NEWS-1'], style: 'value' }],
    advisory: { note: 'advisory display-only' },
  }),
  '/api/dual-line-status': ok({
    line1: { label: 'Line-1 选股', wired: true, max_debates_per_day: 8 },
    line2: { label: 'Line-2 监控', daily_wired: true, intraday_wired: true },
    rotation: { label: '≤5 槽轮动', wired: true, max_total_positions: 5 },
    scheduler_wired: true, note: 'polling',
  }),
  '/api/slot-rotation': ok({
    available: true, note: '', max_total_positions: 5, underinvested_block_active: false,
    open_intent_count: 0, open_intents: [],
    recent_events: [{ event_type: 'proposed', trade_date: '2026-06-12', intent_id: 'i1', incumbent_code: '000001', challenger_code: '600519', outcome_kind: null, buy_code: null, blocks_further_rotation: false, note: '换仓提议' }],
  }),
  '/api/trading/positions': ok([
    { code: '600519', volume: 100, available_volume: 100, cost_price: 1700, market_value: 172430, unrealized_pnl: 2430, unrealized_pnl_pct: 0.0143, stop_loss_line: 1564, stop_loss_distance: 0.08, position_pct: 0.15, risk_status: 'normal', entry_style: 'value' },
    { code: '159949', volume: 200, available_volume: 0, cost_price: 1.2, market_value: 248, unrealized_pnl: 8, unrealized_pnl_pct: 0.033, stop_loss_line: 1.1, stop_loss_distance: 0.083, position_pct: 0.02, risk_status: 'normal', entry_style: 'short_term' },
  ]),
  '/api/trading/accounts': ok([{ account_id: 'default', label: '策略A (默认)', created_at: '2026-04-01T00:00:00+00:00' }]),
  '/api/trading/account': ok({ total_assets: 1_124_300, available_cash: 950_000, frozen_cash: 0, market_value: 172_678, total_pnl: 124_300, total_pnl_pct: 0.1243, initial_capital: 1_000_000 }),
  '/api/trading/orders': ok([]),
  '/api/trading/trades': ok([]),
  '/api/portfolio/equity-points/latest': ok({
    point: { snapshot_at: '2026-06-12T07:00:00+00:00', trade_date: '2026-06-12', cash: 950000, frozen_cash: 0, market_value: 172678, total_equity: 1122678, initial_capital: 1000000, pnl: 122678, pnl_pct: 0.1227, quality: 'FRESH', last_broker_event_id: 42, policy_hash: 'b'.repeat(64), positions: [{ code: '600519', volume: 100, cost_price: 1700, last_price: 1724.3, market_value: 172430, unrealized_pnl: 2430, unrealized_pnl_pct: 0.0143, price_quality: 'FRESH', last_price_at: '2026-06-12T06:59:50+00:00' }] },
    repository_status: 'ok', timestamp: '2026-06-12T07:00:01+00:00',
  }),
  '/api/theme-research/industry-chain': ok({ available: false, note: 'KG 未物化', nodes: [], edges: [], chokepoints: [], theme_peer_sourcing: { pinned_candidate_count: 0, note: '' } }),
}

function matchMock(url) {
  const path = new URL(url).pathname
  if (MOCKS[path]) return MOCKS[path]
  // Prefix matches for parametrised/secondary endpoints.
  if (path.startsWith('/api/risk')) return ok({ system_status: 'normal', run_mode: { simulation_auto: true, feishu_interactive: true } })
  if (path.startsWith('/api/cost') || path.startsWith('/api/system') || path.startsWith('/api/data-quality') || path.startsWith('/api/market') || path.startsWith('/api/news') || path.startsWith('/api/analysis')) return ok({})
  return null
}

const ROUTES = [
  { path: '/performance', name: 'performance', expect: ['.readiness-kpi-panel'] },
  { path: '/system-status', name: 'system-status', expect: ['.autopilot-timeline', '.evolution-panel'] },
  { path: '/instruction-plans', name: 'instruction-plans', expect: ['.instruction-plans'] },
  { path: '/portfolio', name: 'portfolio', expect: ['.position-table'] },
  { path: '/dashboard', name: 'dashboard', expect: [] },
]

const report = { routes: [], manualTrade: null }
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })

await ctx.route('**/api/**', async (route) => {
  const data = matchMock(route.request().url())
  if (data === null) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ok({})) })
  if (route.request().method() !== 'GET') {
    // manual-trades POST etc — never touch a real mirror.
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ok({ external_trade_id: 'UT-20260612-103000-600519-SELL-001', feishu_sent: false, apply_result: { cash_delta: 0, positions_delta: [], broker_event_sequence: null, reason: 'manual_trade_applied' } })) })
  }
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
})

async function examRoute(page, r) {
  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  const pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  await page.goto(`${BASE}${r.path}`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForTimeout(1400)
  const found = {}
  for (const sel of r.expect) found[sel] = (await page.locator(sel).count()) > 0
  const layout = await page.evaluate(() => {
    const doc = document.documentElement
    const cards = [...document.querySelectorAll('.el-card, .source-card, section')]
    const collapsed = cards.filter((el) => { const x = el.getBoundingClientRect(); return x.width > 0 && x.height === 0 }).length
    return { horizontalOverflow: doc.scrollWidth > doc.clientWidth + 2, scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, collapsedCards: collapsed }
  })
  await page.screenshot({ path: `${OUT}/${r.name}.png`, fullPage: true })
  return { path: r.path, name: r.name, expectFound: found, consoleErrors, pageErrors, layout }
}

for (const r of ROUTES) {
  const page = await ctx.newPage()
  try { report.routes.push(await examRoute(page, r)) }
  catch (e) { report.routes.push({ path: r.path, name: r.name, error: String(e) }) }
  await page.close()
}

try {
  const page = await ctx.newPage()
  await page.goto(`${BASE}/portfolio`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForTimeout(1500)
  const btn = page.getByRole('button', { name: '记录手动操作' })
  const btnVisible = (await btn.count()) > 0
  let dialogShown = false, formFields = {}
  if (btnVisible) {
    await btn.first().click()
    await page.waitForTimeout(700)
    dialogShown = (await page.locator('.el-overlay-dialog, .el-dialog').filter({ hasText: '记录手动操作' }).count()) > 0
    formFields = {
      hasReasonSelect: (await page.locator('.manual-trade-form .el-select').count()) > 0,
      hasVolumeInput: (await page.locator('.manual-trade-form .el-input-number').count()) > 0,
    }
    await page.screenshot({ path: `${OUT}/manual-trade-form.png`, fullPage: true })
  }
  report.manualTrade = { btnVisible, dialogShown, formFields }
  await page.close()
} catch (e) { report.manualTrade = { error: String(e) } }

await browser.close()
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
