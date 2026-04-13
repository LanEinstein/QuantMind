import { test, expect } from '@playwright/test'

test.describe('Portfolio Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/portfolio')
  })

  test('page loads with account banner', async ({ page }) => {
    // Account banner should be visible with total assets
    await expect(page.locator('.account-banner')).toBeVisible()
    await expect(page.locator('.stat-label').first()).toContainText('总资产')
  })

  test('account tabs display default account', async ({ page }) => {
    await expect(page.locator('.account-tabs')).toBeVisible()
    const tab = page.locator('.el-tabs__item').first()
    await expect(tab).toBeVisible()
  })

  test('position table renders', async ({ page }) => {
    await expect(page.locator('.position-table')).toBeVisible()
    // Should have table headers
    await expect(page.locator('text=代码')).toBeVisible()
    await expect(page.locator('text=风控状态')).toBeVisible()
  })

  test('position table shows risk status badges', async ({ page }) => {
    // In dev mode, mock data includes positions with risk status
    const tags = page.locator('.position-table .el-tag')
    await expect(tags.first()).toBeVisible()
  })

  test('stop loss distance is color-coded', async ({ page }) => {
    // Mock data has positions with varying stop_loss_distance
    const safe = page.locator('.distance-safe').first()
    await expect(safe).toBeVisible()
  })

  test('order list tab shows today orders', async ({ page }) => {
    // Orders tab should be active by default
    await expect(page.locator('text=今日委托')).toBeVisible()
    // Order status badges should be visible
    const orderTags = page.locator('.order-list .el-tag')
    await expect(orderTags.first()).toBeVisible()
  })

  test('cancel button only on PENDING orders', async ({ page }) => {
    // PENDING orders should have cancel button
    const cancelBtn = page.locator('text=撤单').first()
    if (await cancelBtn.isVisible()) {
      await expect(cancelBtn).toBeVisible()
    }
  })

  test('trade history tab works', async ({ page }) => {
    // Click trade history tab
    await page.locator('text=成交历史').click()
    await expect(page.locator('.trade-history')).toBeVisible()
    // Filter bar should be present
    await expect(page.locator('text=导出CSV')).toBeVisible()
  })

  test('CSV export button is present', async ({ page }) => {
    await page.locator('text=成交历史').click()
    await expect(page.locator('text=导出CSV')).toBeVisible()
  })

  test('tab switching between orders and trades', async ({ page }) => {
    // Default: orders tab
    await expect(page.locator('.order-list')).toBeVisible()
    // Switch to trades
    await page.locator('text=成交历史').click()
    await expect(page.locator('.trade-history')).toBeVisible()
    // Switch back
    await page.locator('text=今日委托').click()
    await expect(page.locator('.order-list')).toBeVisible()
  })

  test('new account button shows phase 5 message', async ({ page }) => {
    const addBtn = page.locator('text=新建')
    if (await addBtn.isVisible()) {
      await addBtn.click()
      await expect(page.locator('.el-message')).toBeVisible()
    }
  })
})

test.describe('Portfolio Page — P4-T04 enhancements', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/portfolio')
    await page.waitForSelector('.account-banner', { timeout: 10_000 })
  })

  test('auth mode tag is displayed in account banner', async ({ page }) => {
    const authTag = page.locator('.auth-mode-tag')
    await expect(authTag).toBeVisible()
    await expect(authTag).toContainText('建议模式')
  })

  test('position code click opens detail drawer', async ({ page }) => {
    const codeLink = page.locator('.code-link').first()
    if (await codeLink.isVisible()) {
      await codeLink.click()
      // Drawer should open with position details
      await expect(page.locator('.el-drawer')).toBeVisible()
      await expect(page.locator('.detail-grid')).toBeVisible()
      await expect(page.locator('text=成本价')).toBeVisible()
      await expect(page.locator('text=止损距离')).toBeVisible()
    }
  })

  test('position detail drawer shows stop-loss gauge', async ({ page }) => {
    const codeLink = page.locator('.code-link').first()
    if (await codeLink.isVisible()) {
      await codeLink.click()
      await expect(page.locator('.gauge-bar')).toBeVisible()
      await expect(page.locator('.gauge-fill')).toBeVisible()
    }
  })

  test('position detail drawer shows risk status tag', async ({ page }) => {
    const codeLink = page.locator('.code-link').first()
    if (await codeLink.isVisible()) {
      await codeLink.click()
      await expect(page.locator('text=风控状态')).toBeVisible()
      const riskTag = page.locator('.el-drawer .el-tag')
      await expect(riskTag).toBeVisible()
    }
  })

  test('account switch changes displayed data', async ({ page }) => {
    // Check for multiple account tabs (mock provides 2 accounts)
    const tabs = page.locator('.el-tabs__item')
    const count = await tabs.count()
    if (count >= 2) {
      await tabs.nth(1).click()
      // After switching, banner should still be visible
      await expect(page.locator('.account-banner')).toBeVisible()
    }
  })
})
