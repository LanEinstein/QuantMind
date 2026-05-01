import { test, expect, type Route } from '@playwright/test'

/**
 * End-to-end test for the AgentDebate view (/agent-debate).
 *
 * Uses page.route() to stub the Phase-5 A1/A2 API contract:
 *   GET  /api/analysis/history            → [AnalysisSummary]
 *   GET  /api/analysis/{record_id}        → AnalysisDetail
 *   POST /api/analysis/jobs               → { job_id, status }
 *   GET  /api/analysis/stream/{job_id}    → text/event-stream body with
 *                                           agent_completed + pipeline_completed
 *
 * Avoids needing a live backend while still exercising the real store /
 * composable / router wiring (no mock-fallback toggle).
 */

const SAMPLE_HISTORY = [
  {
    id: '65d3f1a0000000000000a001',
    run_id: 'run-history-001',
    stock_code: '600519',
    stock_name: '贵州茅台',
    trade_date: '2026-04-24',
    status: 'completed',
    action: '买入',
    confidence: 0.82,
    risk_score: 0.3,
    signal_id: 'signal-xyz',
    created_at: '2026-04-24T09:50:00Z',
    completed_at: '2026-04-24T10:02:00Z',
  },
  {
    id: '65d3f1a0000000000000a002',
    run_id: 'run-history-002',
    stock_code: '000858',
    stock_name: '五粮液',
    trade_date: '2026-04-23',
    status: 'completed',
    action: '持有',
    confidence: 0.55,
    risk_score: 0.42,
    signal_id: null,
    created_at: '2026-04-23T10:00:00Z',
    completed_at: '2026-04-23T10:12:00Z',
  },
]

const SAMPLE_DETAIL = {
  id: '65d3f1a0000000000000a001',
  run_id: 'run-history-001',
  stock_code: '600519',
  stock_name: '贵州茅台',
  trade_date: '2026-04-24',
  status: 'completed',
  max_rounds: 2,
  current_round: 2,
  steps: [],
  analysts: [],
  intelligence_officer: null,
  debates: [
    {
      round: 1,
      bull: {
        role: 'bull',
        round: 1,
        content: '基本面强劲，估值合理',
        evidence: [],
        model: 'Kimi',
        timestamp: '2026-04-24T09:52:00Z',
      },
      bear: {
        role: 'bear',
        round: 1,
        content: '外资连续减持，技术面走弱',
        evidence: [],
        model: 'Kimi',
        timestamp: '2026-04-24T09:54:00Z',
      },
    },
  ],
  risk_assessment: {
    model: 'Kimi',
    checks: [],
    position_limit: '15%',
    raw_text: '流动性充足，建议控制仓位',
  },
  decision: {
    model: 'Kimi',
    score: 82,
    score_label: '偏多',
    action: '买入',
    target_price: 1900,
    stop_loss: null,
    position_pct: null,
    reasoning: '综合多空辩论和风控评估，建议买入',
    confidence: 0.82,
    risk_score: 0.3,
  },
  signal_id: 'signal-xyz',
  created_at: '2026-04-24T09:50:00Z',
  completed_at: '2026-04-24T10:02:00Z',
  error: null,
}

function jsonEnvelope(route: Route, data: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify({ status: status < 400 ? 'ok' : 'error', data, error: null }),
  })
}

test.describe('AgentDebate page', () => {
  test('loads history from backend and renders items', async ({ page }) => {
    await page.route('**/api/analysis/history**', (route) =>
      jsonEnvelope(route, SAMPLE_HISTORY),
    )

    await page.goto('/agent-debate')
    await expect(page.locator('.debate-layout')).toBeVisible()

    // History sidebar renders the two intercepted items.
    const items = page.locator('.history-item')
    await expect(items).toHaveCount(2, { timeout: 5000 })
    await expect(items.first()).toContainText('600519')
    await expect(items.first()).toContainText('贵州茅台')
    await expect(items.first()).toContainText('买入')
  })

  test('clicking a history item loads full detail', async ({ page }) => {
    await page.route('**/api/analysis/history**', (route) =>
      jsonEnvelope(route, SAMPLE_HISTORY),
    )
    await page.route('**/api/analysis/65d3f1a0000000000000a001', (route) =>
      jsonEnvelope(route, SAMPLE_DETAIL),
    )

    await page.goto('/agent-debate')
    await page.locator('.history-item').first().click()

    // Stock banner populated
    await expect(page.locator('.stock-banner')).toContainText('600519')
    await expect(page.locator('.stock-banner')).toContainText('贵州茅台')

    // Debate content surfaces from the fetched detail
    await expect(page.getByText('基本面强劲，估值合理').first()).toBeVisible()
    await expect(page.getByText('外资连续减持，技术面走弱').first()).toBeVisible()

    // Decision / risk rendered
    await expect(
      page.getByText('综合多空辩论和风控评估，建议买入').first(),
    ).toBeVisible()
  })

  test('start analysis triggers POST /jobs and consumes SSE stream', async ({
    page,
  }) => {
    const JOB_ID = 'job-123e4567'

    await page.route('**/api/analysis/history**', (route) =>
      jsonEnvelope(route, SAMPLE_HISTORY),
    )
    await page.route('**/api/analysis/jobs', (route) =>
      jsonEnvelope(route, { job_id: JOB_ID, status: 'running' }),
    )
    await page.route(`**/api/analysis/stream/${JOB_ID}`, async (route) => {
      const completedEvent = {
        event_type: 'agent_completed',
        agent: 'bull_researcher',
        round: 1,
        content: '看多论点（SSE）',
        model_label: 'Kimi',
        model_id: 'kimi-k2.6',
        status: 'completed',
        error: null,
        timestamp: '2026-04-24T10:00:00Z',
        run_id: JOB_ID,
      }
      const pipelineEvent = {
        event_type: 'pipeline_completed',
        run_id: JOB_ID,
        record_id: '65d3f1a0000000000000a001',
        signal_id: 'signal-xyz',
        timestamp: '2026-04-24T10:02:00Z',
      }
      const body =
        `data: ${JSON.stringify(completedEvent)}\n\n` +
        `data: ${JSON.stringify(pipelineEvent)}\n\n`
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'Cache-Control': 'no-cache',
          'X-Accel-Buffering': 'no',
        },
        body,
      })
    })
    await page.route('**/api/analysis/65d3f1a0000000000000a001', (route) =>
      jsonEnvelope(route, SAMPLE_DETAIL),
    )

    // We need a stock selected to enable the button. The selector is a
    // remote/filterable el-select that eagerly renders a few options.
    await page.goto('/agent-debate')
    await page.locator('.stock-selector').click()
    await page.locator('.el-select-dropdown__item').first().click()

    const createJobRequest = page.waitForRequest(
      (req) =>
        req.url().includes('/api/analysis/jobs') && req.method() === 'POST',
    )

    await page.getByRole('button', { name: '开始分析' }).click()
    await createJobRequest

    // Debate content eventually surfaces from the streamed SSE + fetched detail.
    await expect(
      page.getByText('综合多空辩论和风控评估，建议买入').first(),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('live SSE debate content surfaces before final detail fetch', async ({
    page,
  }) => {
    const JOB_ID = 'job-live-888'

    await page.route('**/api/analysis/history**', (route) =>
      jsonEnvelope(route, SAMPLE_HISTORY),
    )
    await page.route('**/api/analysis/jobs', (route) =>
      jsonEnvelope(route, { job_id: JOB_ID, status: 'running' }),
    )
    await page.route(`**/api/analysis/stream/${JOB_ID}`, async (route) => {
      const bullEvent = {
        event_type: 'agent_completed',
        agent: 'bull_researcher',
        round: 1,
        content: 'LIVE看多：机构持仓上升（SSE实时）',
        model_label: 'Qwen',
        model_id: 'qwen-3.6-plus',
        status: 'completed',
        error: null,
        timestamp: '2026-04-24T10:00:00Z',
        run_id: JOB_ID,
      }
      const pipelineEvent = {
        event_type: 'pipeline_completed',
        run_id: JOB_ID,
        record_id: '65d3f1a0000000000000a001',
        signal_id: 'signal-xyz',
        timestamp: '2026-04-24T10:02:00Z',
      }
      const body =
        `data: ${JSON.stringify(bullEvent)}\n\n` +
        `data: ${JSON.stringify(pipelineEvent)}\n\n`
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'Cache-Control': 'no-cache',
          'X-Accel-Buffering': 'no',
        },
        body,
      })
    })

    // Gate the final detail fetch: only fulfill once the test has
    // already asserted that the live SSE text is visible. Without this
    // gate a regression that drops live events (and relies purely on
    // the detail fetch) would still pass.
    let releaseDetail: (() => void) | null = null
    const detailReady = new Promise<void>((resolve) => {
      releaseDetail = resolve
    })
    await page.route('**/api/analysis/65d3f1a0000000000000a001', async (route) => {
      await detailReady
      await jsonEnvelope(route, SAMPLE_DETAIL)
    })

    await page.goto('/agent-debate')
    await page.locator('.stock-selector').click()
    await page.locator('.el-select-dropdown__item').first().click()
    await page.getByRole('button', { name: '开始分析' }).click()

    // BEFORE the detail resolves, the streamed live debate text must
    // already be rendered — proving the provisional-detail + applySSEEvent
    // pathway is actually streaming, not just waiting for detail.
    await expect(
      page.getByText('LIVE看多：机构持仓上升（SSE实时）').first(),
    ).toBeVisible({ timeout: 10_000 })

    // Release the detail fetch; the final decision text then appears.
    releaseDetail?.()
    await expect(
      page.getByText('综合多空辩论和风控评估，建议买入').first(),
    ).toBeVisible({ timeout: 10_000 })
  })
})
