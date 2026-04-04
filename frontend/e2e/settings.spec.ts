import { test, expect } from '@playwright/test'

test.describe('Settings Layout', () => {
  test('page loads and redirects to llm-router', async ({ page }) => {
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings\/llm-router/)
    await expect(page.locator('.settings-layout')).toBeVisible()
  })

  test('tab navigation between sub-pages', async ({ page }) => {
    await page.goto('/settings/llm-router')
    await expect(page.locator('.settings-tabs')).toBeVisible()

    // Navigate to Data Sources
    await page.click('text=数据源')
    await expect(page).toHaveURL(/\/settings\/data-sources/)

    // Navigate to MiroFish
    await page.click('text=MiroFish配置')
    await expect(page).toHaveURL(/\/settings\/mirofish/)

    // Navigate to Cost Dashboard
    await page.click('text=成本统计')
    await expect(page).toHaveURL(/\/settings\/cost-dashboard/)

    // Navigate back to LLM Router
    await page.click('text=LLM路由配置')
    await expect(page).toHaveURL(/\/settings\/llm-router/)
  })

  test('all four tabs are visible', async ({ page }) => {
    await page.goto('/settings')
    const tabs = page.locator('.el-tabs__item')
    await expect(tabs).toHaveCount(4)
  })
})

test.describe('LLM Router Config Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings/llm-router')
    // Wait for mock data to load (API call fails, falls back to dev mock)
    await page.waitForTimeout(2000)
  })

  test('expansion slots show grayed-out state', async ({ page }) => {
    const slots = page.locator('.expansion-slot')
    await expect(slots.first()).toBeVisible()
    await expect(page.locator('text=待接入').first()).toBeVisible()
  })

  test('provider cards render after data loads', async ({ page }) => {
    // Provider cards render from store data, may need extra wait
    const card = page.locator('.provider-card').first()
    await expect(card).toBeVisible({ timeout: 10000 })
  })

  test('page contains llm-router layout sections', async ({ page }) => {
    await expect(page.locator('.llm-router-page')).toBeVisible()
    // Mapping card should always render (even if chart is empty)
    await expect(page.locator('.mapping-card')).toBeVisible()
    // Agent table card should render
    await expect(page.locator('.agent-table-card')).toBeVisible()
  })

  test('agent-model mapping card has chart container', async ({ page }) => {
    const chart = page.locator('.mapping-chart')
    await expect(chart).toBeVisible()
  })

  test('agent table card renders', async ({ page }) => {
    await expect(page.locator('.agent-table-card')).toBeVisible()
    await expect(page.locator('.agent-table-card .card-title')).toContainText('Agent配置详情')
  })
})

test.describe('Data Sources Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings/data-sources')
    await page.waitForTimeout(1500)
  })

  test('data source table renders', async ({ page }) => {
    await expect(page.locator('.el-table')).toBeVisible()
    const rows = page.locator('.el-table__row')
    const count = await rows.count()
    expect(count).toBeGreaterThanOrEqual(1)
  })

  test('refresh all button exists', async ({ page }) => {
    await expect(page.locator('text=刷新全部')).toBeVisible()
  })

  test('auto-refresh hint shown', async ({ page }) => {
    await expect(page.locator('.auto-refresh-hint')).toBeVisible()
    await expect(page.locator('.auto-refresh-hint')).toContainText('60秒')
  })

  test('test buttons present in each row', async ({ page }) => {
    const testBtns = page.locator('.el-table__row button')
    await expect(testBtns.first()).toBeVisible()
  })
})

test.describe('MiroFish Config Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings/mirofish')
    await page.waitForTimeout(1500)
  })

  test('config form renders with sliders', async ({ page }) => {
    await expect(page.locator('.config-form')).toBeVisible()
    const sliders = page.locator('.el-slider')
    const count = await sliders.count()
    expect(count).toBeGreaterThanOrEqual(3)
  })

  test('save button is present', async ({ page }) => {
    await expect(page.locator('button:has-text("保存")')).toBeVisible()
  })

  test('reset button is present', async ({ page }) => {
    await expect(page.locator('button:has-text("重置")')).toBeVisible()
  })

  test('cost estimate card shows values', async ({ page }) => {
    await expect(page.locator('.estimate-card')).toBeVisible()
    const values = page.locator('.estimate-value')
    const count = await values.count()
    expect(count).toBeGreaterThanOrEqual(2)
  })

  test('model select dropdown exists', async ({ page }) => {
    await expect(page.locator('.el-select')).toBeVisible()
  })
})

test.describe('Cost Dashboard Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings/cost-dashboard')
    await page.waitForTimeout(1500)
  })

  test('summary stat cards render', async ({ page }) => {
    const cards = page.locator('.stat-card')
    await expect(cards.first()).toBeVisible()
    const count = await cards.count()
    expect(count).toBeGreaterThanOrEqual(4)
  })

  test('period toggle buttons exist', async ({ page }) => {
    await expect(page.locator('.el-radio-group')).toBeVisible()
    await expect(page.locator('text=最近7天')).toBeVisible()
    await expect(page.locator('text=最近30天')).toBeVisible()
  })

  test('period toggle changes selection', async ({ page }) => {
    await page.click('text=最近7天')
    const cards = page.locator('.stat-card')
    await expect(cards.first()).toBeVisible()

    await page.click('text=最近30天')
    await expect(cards.first()).toBeVisible()
  })

  test('cost trend chart container renders', async ({ page }) => {
    const chart = page.locator('.trend-chart')
    await expect(chart).toBeVisible()
  })

  test('pie chart container renders', async ({ page }) => {
    const chart = page.locator('.pie-chart')
    await expect(chart).toBeVisible()
  })

  test('per-agent table renders', async ({ page }) => {
    const table = page.locator('.el-table')
    await expect(table).toBeVisible()
  })
})
