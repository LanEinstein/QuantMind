import { test, expect } from '@playwright/test'

test.describe('Simulation Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/simulation')
    // Wait for page to load (mock data in DEV mode)
    await page.waitForSelector('.simulation-layout', { timeout: 10_000 })
  })

  test('page loads with header and event title', async ({ page }) => {
    const header = page.locator('.simulation-header')
    await expect(header).toBeVisible()

    const title = page.locator('.event-title')
    await expect(title).toBeVisible()
    await expect(title).not.toHaveText('事态推演') // Should show actual event title from mock
  })

  test('importance badge displays with correct score', async ({ page }) => {
    const badge = page.locator('.importance-badge')
    await expect(badge).toBeVisible()
    await expect(badge).toContainText('/10')
  })

  test('simulation meta items display agent count and rounds', async ({
    page,
  }) => {
    const metaItems = page.locator('.meta-item')
    await expect(metaItems.first()).toBeVisible()
    // Should show "300 Agents" from mock data
    await expect(page.locator('.meta-item').nth(0)).toContainText('Agents')
    await expect(page.locator('.meta-item').nth(1)).toContainText('Rounds')
  })

  test('sentiment chart renders in Zone A', async ({ page }) => {
    const chartCard = page.locator('.zone-a-card')
    await expect(chartCard).toBeVisible()

    // VChart canvas should render
    const canvas = chartCard.locator('canvas')
    await expect(canvas).toBeVisible({ timeout: 5000 })
  })

  test('play button exists and is clickable', async ({ page }) => {
    const playBtn = page.locator('.chart-toolbar .el-button')
    await expect(playBtn).toBeVisible()

    // Click play
    await playBtn.click()

    // Round indicator should show R1
    const indicator = page.locator('.round-indicator')
    await expect(indicator).toContainText('R')
  })

  test('hidden variable matrix renders in Zone B', async ({ page }) => {
    const chartCard = page.locator('.zone-b-card')
    await expect(chartCard).toBeVisible()

    // Should have collapse items
    const collapseItems = chartCard.locator('.el-collapse-item')
    await expect(collapseItems.first()).toBeVisible()

    // Should show probability bars
    const bars = chartCard.locator('.bar-fill')
    const count = await bars.count()
    expect(count).toBeGreaterThan(0)
  })

  test('clicking a hidden variable expands reasoning', async ({ page }) => {
    const firstItem = page.locator('.el-collapse-item').first()
    await firstItem.locator('.el-collapse-item__header').click()

    // Reasoning text and disclaimer should appear
    const reasoning = firstItem.locator('.reasoning-text')
    await expect(reasoning).toBeVisible({ timeout: 2000 })

    const disclaimer = firstItem.locator('.disclaimer')
    await expect(disclaimer).toBeVisible()
    await expect(disclaimer).toContainText('仿真估计')
  })

  test('inflection timeline renders in Zone C', async ({ page }) => {
    const chartCard = page.locator('.zone-c-card')
    await expect(chartCard).toBeVisible()

    // Should show timeline items
    const items = chartCard.locator('.timeline-item')
    const count = await items.count()
    expect(count).toBeGreaterThan(0)

    // Each item should have a day tag
    const dayTag = items.first().locator('.el-tag')
    await expect(dayTag).toContainText('Day')
  })

  test('extreme scenario pie chart renders in Zone D', async ({ page }) => {
    const chartCard = page.locator('.zone-d-card')
    await expect(chartCard).toBeVisible()

    // VChart canvas should render
    const canvas = chartCard.locator('canvas')
    await expect(canvas).toBeVisible({ timeout: 5000 })
  })

  test('recommendation bar displays advice text', async ({ page }) => {
    const rec = page.locator('.recommendation-bar')
    await expect(rec).toBeVisible()
    await expect(rec.locator('.recommendation-text')).not.toBeEmpty()
  })

  test('bottom action buttons are present', async ({ page }) => {
    const actions = page.locator('.bottom-actions')
    await expect(actions).toBeVisible()

    await expect(actions.locator('button')).toHaveCount(4)
  })

  test('full report dialog opens on button click', async ({ page }) => {
    const reportBtn = page.locator('.bottom-actions button').first()
    await reportBtn.click()

    const dialog = page.locator('.el-dialog')
    await expect(dialog).toBeVisible({ timeout: 2000 })
    await expect(dialog).toContainText('仿真完整报告')
  })

  test('history sidebar displays past simulations', async ({ page }) => {
    const sidebar = page.locator('.history-sidebar')
    await expect(sidebar).toBeVisible()

    const header = sidebar.locator('.sidebar-header')
    await expect(header).toHaveText('仿真历史')

    // Should show history items from mock data
    const items = sidebar.locator('.history-item')
    const count = await items.count()
    expect(count).toBeGreaterThan(0)
  })

  test('sidebar search filters history items', async ({ page }) => {
    const searchInput = page.locator('.sidebar-search input')
    await searchInput.fill('美联储')
    await page.waitForTimeout(400) // debounce

    // Should filter to matching items
    const items = page.locator('.history-item')
    const count = await items.count()
    expect(count).toBeLessThanOrEqual(5)
  })

  test('clicking history item highlights it as active', async ({ page }) => {
    const items = page.locator('.history-item')
    const secondItem = items.nth(1)

    await secondItem.click()
    await expect(secondItem).toHaveClass(/active/)
  })

  test('navigate to agent debate button works', async ({ page }) => {
    // Click the last action button (注入Agent辩论)
    const debateBtn = page.locator('.bottom-actions button').last()
    await debateBtn.click()

    await page.waitForURL('**/agent-debate', { timeout: 5000 })
    expect(page.url()).toContain('/agent-debate')
  })
})
