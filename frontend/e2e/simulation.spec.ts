import { test, expect, type Page } from '@playwright/test'

async function waitForLoaded(page: Page) {
  await page.goto('/simulation')
  await page.waitForSelector('.simulation-layout', { timeout: 10_000 })
  await page.waitForSelector('.scrubber-bar', { timeout: 10_000 })
}

async function getScrubberRound(page: Page): Promise<number> {
  const text = await page.locator('.scrubber-label').innerText()
  const match = text.match(/R(\d+)/)
  return match ? Number(match[1]) : 0
}

test.describe('Simulation Page — header and meta', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('page loads with header and event title', async ({ page }) => {
    await expect(page.locator('.simulation-header')).toBeVisible()
    const title = page.locator('.event-title')
    await expect(title).toBeVisible()
    await expect(title).not.toHaveText('事态推演')
  })

  test('importance badge displays with correct score', async ({ page }) => {
    const badge = page.locator('.importance-badge')
    await expect(badge).toBeVisible()
    await expect(badge).toContainText('/10')
  })

  test('simulation meta items display agent count and rounds', async ({ page }) => {
    await expect(page.locator('.meta-item').nth(0)).toContainText('Agents')
    await expect(page.locator('.meta-item').nth(1)).toContainText('Rounds')
  })
})

test.describe('Simulation Page — global playback clock', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('scrubber bar exists with play/pause button and range slider', async ({ page }) => {
    await expect(page.locator('.scrubber-bar')).toBeVisible()
    await expect(page.locator('.scrubber-input')).toBeVisible()
    await expect(page.locator('.scrubber-play-btn')).toBeVisible()
    await expect(page.locator('.scrubber-total')).toContainText('/ 20')
  })

  test('autoplay starts on mount and advances the scrubber', async ({ page }) => {
    const initial = await getScrubberRound(page)
    await page.waitForTimeout(900)
    const later = await getScrubberRound(page)
    expect(later).toBeGreaterThan(initial)
  })

  test('play/pause toggle button flips between ▶ and ⏸', async ({ page }) => {
    const btn = page.locator('.scrubber-play-btn')
    // Autoplay is on, so button initially shows ⏸
    await expect(btn).toContainText('⏸')
    await btn.click()
    await expect(btn).toContainText('▶')
    await btn.click()
    await expect(btn).toContainText('⏸')
  })

  test('Space keyboard shortcut toggles play state', async ({ page }) => {
    const btn = page.locator('.scrubber-play-btn')
    await expect(btn).toContainText('⏸')
    await page.keyboard.press('Space')
    await expect(btn).toContainText('▶')
    await page.keyboard.press('Space')
    await expect(btn).toContainText('⏸')
  })

  test('ArrowRight steps forward by 1 round when paused', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click() // pause
    const before = await getScrubberRound(page)
    await page.keyboard.press('ArrowRight')
    const after = await getScrubberRound(page)
    expect(after).toBe(Math.min(before + 1, 20))
  })

  test('ArrowLeft steps back by 1 round when paused', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click() // pause
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('ArrowRight')
    const before = await getScrubberRound(page)
    await page.keyboard.press('ArrowLeft')
    const after = await getScrubberRound(page)
    expect(after).toBe(before - 1)
  })

  test('Home key jumps to round 1', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click() // pause
    await page.keyboard.press('End')
    await page.keyboard.press('Home')
    const round = await getScrubberRound(page)
    expect(round).toBe(1)
  })

  test('End key jumps to the final round', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click() // pause
    await page.keyboard.press('End')
    const round = await getScrubberRound(page)
    expect(round).toBe(20)
  })
})

test.describe('Simulation Page — focus mode', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('F keyboard shortcut toggles body.focus-mode class', async ({ page }) => {
    const bodyClasses = () => page.evaluate(() => document.body.className)
    expect(await bodyClasses()).not.toContain('focus-mode')
    await page.keyboard.press('KeyF')
    expect(await bodyClasses()).toContain('focus-mode')
    await page.keyboard.press('KeyF')
    expect(await bodyClasses()).not.toContain('focus-mode')
  })
})

test.describe('Simulation Page — charts', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('sentiment chart renders canvas in Zone A', async ({ page }) => {
    const card = page.locator('.zone-a-card')
    await expect(card).toBeVisible()
    await expect(card.locator('canvas').first()).toBeVisible({ timeout: 5000 })
  })

  test('hidden variable matrix renders in Zone B', async ({ page }) => {
    const card = page.locator('.zone-b-card')
    await expect(card).toBeVisible()
    const headers = card.locator('.var-header')
    const count = await headers.count()
    expect(count).toBeGreaterThan(0)
    // Consensus underlay + probability bar both render
    await expect(card.locator('.bar-fill').first()).toBeVisible()
    await expect(card.locator('.bar-fill-consensus').first()).toBeVisible()
  })

  test('inflection timeline renders in Zone C with Day tags', async ({ page }) => {
    // Jump to end so all inflection points (filtered by playback) are visible
    await page.locator('.scrubber-play-btn').click()
    await page.keyboard.press('End')
    const card = page.locator('.zone-c-card')
    await expect(card).toBeVisible()
    const items = card.locator('.timeline-item')
    const count = await items.count()
    expect(count).toBeGreaterThan(0)
    await expect(items.first().locator('.el-tag')).toContainText('Day')
  })

  test('inflection marker dots vary in size based on confidence', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click()
    await page.keyboard.press('End')
    const dots = page.locator('.zone-c-card .marker-dot')
    const count = await dots.count()
    expect(count).toBeGreaterThanOrEqual(2)
    const sizes = new Set<number>()
    for (let i = 0; i < count; i++) {
      const box = await dots.nth(i).boundingBox()
      if (box) sizes.add(Math.round(box.width))
    }
    // Mock has 4 inflection points with varying confidence (0.65, 0.72, 0.78, 0.88)
    // → varying dot widths (8 + confidence * 8)
    expect(sizes.size).toBeGreaterThanOrEqual(2)
  })

  test('inflection dots carry type-specific class colors', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click()
    await page.keyboard.press('End')
    const typed = page.locator(
      '.zone-c-card .marker-dot.type-reversal, .zone-c-card .marker-dot.type-convergence, .zone-c-card .marker-dot.type-cascade, .zone-c-card .marker-dot.type-exhaustion',
    )
    const count = await typed.count()
    expect(count).toBeGreaterThan(0)
  })

  test('clicking an inflection item seeks the global clock', async ({ page }) => {
    await page.locator('.scrubber-play-btn').click() // pause
    await page.keyboard.press('End') // jump to final round so all inflections are visible
    await page.locator('.zone-c-card .timeline-item').first().click()
    const round = await getScrubberRound(page)
    // First mock inflection is at day=3
    expect(round).toBe(3)
  })

  test('extreme scenario pie renders canvas in Zone D', async ({ page }) => {
    const card = page.locator('.zone-d-card')
    await expect(card).toBeVisible()
    await expect(card.locator('canvas').first()).toBeVisible({ timeout: 5000 })
  })
})

test.describe('Simulation Page — recommendation demotion', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('recommendation bar renders as muted italic 12px footer', async ({ page }) => {
    const rec = page.locator('.recommendation-bar')
    await expect(rec).toBeVisible()
    const text = rec.locator('.recommendation-text')
    await expect(text).not.toBeEmpty()

    const style = await text.evaluate((el) => {
      const computed = window.getComputedStyle(el)
      return {
        fontSize: computed.fontSize,
        fontStyle: computed.fontStyle,
      }
    })
    expect(style.fontSize).toBe('12px')
    expect(style.fontStyle).toBe('italic')
  })
})

test.describe('Simulation Page — history sidebar', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('history sidebar displays past simulations', async ({ page }) => {
    const sidebar = page.locator('.history-sidebar')
    await expect(sidebar).toBeVisible()
    await expect(sidebar.locator('.sidebar-header')).toHaveText('仿真历史')
    const items = sidebar.locator('.history-item')
    expect(await items.count()).toBeGreaterThan(0)
  })

  test('sidebar search filters history items', async ({ page }) => {
    await page.locator('.sidebar-search input').fill('美联储')
    await page.waitForTimeout(400)
    const items = page.locator('.history-item')
    expect(await items.count()).toBeLessThanOrEqual(5)
  })

  test('clicking history item highlights it as active', async ({ page }) => {
    const items = page.locator('.history-item')
    const second = items.nth(1)
    await second.click()
    await expect(second).toHaveClass(/active/)
  })
})

test.describe('Simulation Page — dialogs and navigation', () => {
  test.beforeEach(async ({ page }) => {
    await waitForLoaded(page)
  })

  test('full report dialog opens on button click', async ({ page }) => {
    await page.locator('.bottom-actions button').first().click()
    const dialog = page.locator('.el-dialog')
    await expect(dialog).toBeVisible({ timeout: 2000 })
    await expect(dialog).toContainText('仿真完整报告')
  })

  test('bottom actions contain exactly 4 buttons', async ({ page }) => {
    await expect(page.locator('.bottom-actions button')).toHaveCount(4)
  })

  test('navigate to agent debate button works', async ({ page }) => {
    await page.locator('.bottom-actions button').last().click()
    await page.waitForURL('**/agent-debate', { timeout: 5000 })
    expect(page.url()).toContain('/agent-debate')
  })
})
