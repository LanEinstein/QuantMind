/**
 * Live front-back integration exam (2026-06-16, pre-open verification).
 *
 * Unlike ad-playwright-exam.mjs (which MOCKS every /api response for a
 * deterministic UI-only exam), this harness drives a real chromium against
 * the LIVE dev stack (vite :9276 → proxy → uvicorn :8001) with NO route
 * interception. It proves the real front-back wiring:
 *   - every /api/* request the page fires, with its real HTTP status
 *   - whether the /ws websocket connects
 *   - console errors + uncaught page errors
 *   - a screenshot per page for visual review
 *
 * READ-ONLY: only navigates (GET). Never clicks submit/action buttons, so
 * it cannot trigger the 3 write endpoints or any feishu send.
 *
 * Usage:  node scripts/live-integration-exam.mjs
 * Env:    EXAM_BASE (default http://127.0.0.1:9276)
 * Out:    screenshots → /tmp/live_exam/ ; JSON report → stdout
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

const BASE = process.env.EXAM_BASE || 'http://127.0.0.1:9276'
const OUT = '/tmp/live_exam'
mkdirSync(OUT, { recursive: true })

const PAGES = [
  '/dashboard',
  '/system-status',
  '/instruction-plans',
  '/portfolio',
  '/execution-reports',
  '/reconciliation-center',
  '/performance',
  '/acceptance-reports',
  '/risk-center',
  '/agent-debate',
  '/data-quality',
  '/feishu-messages',
  '/cost-breakdown',
  '/settings/llm-router',
  '/settings/data-sources',
  '/settings/mirofish',
  '/settings/cost-dashboard',
]

const slug = (p) => p.replace(/^\//, '').replace(/\//g, '_') || 'root'

const run = async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const report = []

  for (const path of PAGES) {
    const page = await ctx.newPage()
    const apiCalls = []
    const consoleErrors = []
    const pageErrors = []
    let wsConnected = false

    page.on('response', (resp) => {
      const url = resp.url()
      if (url.includes('/api/')) {
        apiCalls.push({ url: url.replace(BASE, ''), status: resp.status() })
      }
    })
    page.on('websocket', (ws) => {
      if (ws.url().includes('/ws')) wsConnected = true
    })
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 300))
    })
    page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 300)))

    let nav = 'ok'
    try {
      await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle', timeout: 20_000 })
    } catch (e) {
      // networkidle can time out on pages holding an open SSE/WS — fall back
      // to domcontentloaded so we still capture what loaded.
      nav = `networkidle_timeout: ${String(e).slice(0, 120)}`
      try {
        await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 10_000 })
      } catch (e2) {
        nav = `nav_failed: ${String(e2).slice(0, 160)}`
      }
    }
    // give late XHR / WS a moment
    await page.waitForTimeout(2500)

    const title = await page.title().catch(() => '')
    const bodyText = (await page.evaluate(() => document.body?.innerText || '').catch(() => '')).slice(0, 400)
    await page.screenshot({ path: `${OUT}/${slug(path)}.png`, fullPage: true }).catch(() => {})

    const failedApi = apiCalls.filter((c) => c.status >= 400)
    report.push({
      path,
      nav,
      title,
      api_total: apiCalls.length,
      api_failed: failedApi,
      ws_connected: wsConnected,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      body_empty: bodyText.trim().length === 0,
    })
    await page.close()
  }

  await browser.close()

  const summary = {
    base: BASE,
    pages: report.length,
    pages_with_failed_api: report.filter((r) => r.api_failed.length > 0).map((r) => r.path),
    pages_with_console_errors: report.filter((r) => r.console_errors.length > 0).map((r) => r.path),
    pages_with_page_errors: report.filter((r) => r.page_errors.length > 0).map((r) => r.path),
    pages_nav_problem: report.filter((r) => r.nav !== 'ok').map((r) => ({ path: r.path, nav: r.nav })),
    pages_body_empty: report.filter((r) => r.body_empty).map((r) => r.path),
    pages_ws_connected: report.filter((r) => r.ws_connected).map((r) => r.path),
    total_api_calls: report.reduce((n, r) => n + r.api_total, 0),
  }
  writeFileSync(`${OUT}/report.json`, JSON.stringify({ summary, report }, null, 2))
  console.log(JSON.stringify({ summary, report }, null, 2))
}

run().catch((e) => {
  console.error('EXAM_FATAL', e)
  process.exit(1)
})
