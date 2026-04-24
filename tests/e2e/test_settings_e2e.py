"""
E2E tests for Settings pages using Playwright.

Requires:
    - Frontend dev server running at localhost:9276
    - Backend API running at localhost:8000
    - Or: E2E_BASE_URL set to frontend URL

These tests use Playwright's async API and are designed to be run
with: npx playwright test tests/e2e/test_settings_e2e.py
or:   python -m pytest tests/e2e/test_settings_e2e.py -v

Note: In CI, these tests may use mock data from the frontend dev mode.
"""

from __future__ import annotations

import os
from urllib.error import URLError
from urllib.request import urlopen

import pytest

# Conditionally import playwright — skip tests if not installed
try:
    from playwright.async_api import Page, async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:9276")


def _frontend_is_quantmind(url: str, timeout: float = 1.0) -> bool:
    """Confirm the URL serves the QuantMind frontend (not just any server).

    TCP-only probe is unreliable: another app (e.g. Open WebUI) may occupy
    the port, causing playwright to time out on selectors. We do a cheap
    HTTP GET and sniff the title/root mount point to be sure.
    """
    try:
        with urlopen(url, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
    except (URLError, OSError, ValueError):
        return False
    return "QuantMind" in body or 'id="app"' in body


FRONTEND_UP = _frontend_is_quantmind(BASE_URL)

pytestmark = [
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed"),
    pytest.mark.skipif(
        not FRONTEND_UP,
        reason=f"frontend dev server not reachable at {BASE_URL}",
    ),
    pytest.mark.e2e,
]


@pytest.fixture()
async def page():
    """Create a Playwright browser page for each test."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        pg = await context.new_page()
        yield pg
        await context.close()
        await browser.close()


# --- Settings page navigation ---


async def test_settings_page_loads(page: Page) -> None:
    """Navigate to Settings, verify page renders without errors."""
    await page.goto(f"{BASE_URL}/settings")
    await page.wait_for_selector(".settings-layout", timeout=10000)

    # Tab navigation should be visible
    tabs = page.locator(".el-tabs__item")
    assert await tabs.count() >= 4

    # Default redirect should land on LLM Router
    await page.wait_for_url("**/settings/llm-router")


async def test_settings_tab_navigation(page: Page) -> None:
    """Click each tab and verify navigation."""
    await page.goto(f"{BASE_URL}/settings/llm-router")
    await page.wait_for_selector(".settings-layout", timeout=10000)

    # Click Data Sources tab
    await page.click('text=数据源')
    await page.wait_for_url("**/settings/data-sources")

    # Click MiroFish tab
    await page.click('text=MiroFish配置')
    await page.wait_for_url("**/settings/mirofish")

    # Click Cost tab
    await page.click('text=成本统计')
    await page.wait_for_url("**/settings/cost-dashboard")

    # Click back to LLM Router
    await page.click('text=LLM路由配置')
    await page.wait_for_url("**/settings/llm-router")


# --- LLM Router Config Page ---


async def test_llm_router_shows_providers(page: Page) -> None:
    """Verify provider cards are rendered."""
    await page.goto(f"{BASE_URL}/settings/llm-router")
    await page.wait_for_selector(".provider-card", timeout=10000)

    # Should have at least 3 active provider cards + 2 expansion slots
    cards = page.locator(".provider-card")
    count = await cards.count()
    assert count >= 3


async def test_llm_router_shows_agents_table(page: Page) -> None:
    """Verify agent configuration table renders."""
    await page.goto(f"{BASE_URL}/settings/llm-router")
    await page.wait_for_selector(".agent-table", timeout=10000)

    # Table should have rows
    rows = page.locator(".agent-table .el-table__row")
    count = await rows.count()
    assert count >= 1


async def test_test_connection_button_exists(page: Page) -> None:
    """Verify test connection buttons are present on provider cards."""
    await page.goto(f"{BASE_URL}/settings/llm-router")
    await page.wait_for_selector(".provider-card", timeout=10000)

    buttons = page.locator("text=测试连接")
    count = await buttons.count()
    assert count >= 1


# --- Data Sources Page ---


async def test_data_sources_table_renders(page: Page) -> None:
    """Navigate to Data Sources and verify table renders."""
    await page.goto(f"{BASE_URL}/settings/data-sources")
    await page.wait_for_selector(".el-table", timeout=10000)

    # Should have rows for data sources
    rows = page.locator(".el-table__row")
    count = await rows.count()
    assert count >= 1


async def test_data_sources_refresh_button(page: Page) -> None:
    """Verify refresh button exists and is clickable."""
    await page.goto(f"{BASE_URL}/settings/data-sources")
    await page.wait_for_selector(".page-actions", timeout=10000)

    refresh_btn = page.locator("text=刷新全部")
    assert await refresh_btn.count() == 1


# --- MiroFish Config Page ---


async def test_mirofish_form_renders(page: Page) -> None:
    """Verify MiroFish config form renders with inputs."""
    await page.goto(f"{BASE_URL}/settings/mirofish")
    await page.wait_for_selector(".config-form", timeout=10000)

    # Check sliders exist
    sliders = page.locator(".el-slider")
    assert await sliders.count() >= 3

    # Check save button
    save_btn = page.locator("text=保存")
    assert await save_btn.count() >= 1


async def test_mirofish_cost_estimate_visible(page: Page) -> None:
    """Verify cost estimate card is rendered."""
    await page.goto(f"{BASE_URL}/settings/mirofish")
    await page.wait_for_selector(".estimate-card", timeout=10000)

    estimate = page.locator(".estimate-value")
    assert await estimate.count() >= 1


# --- Cost Dashboard Page ---


async def test_cost_dashboard_charts_render(page: Page) -> None:
    """Verify cost dashboard renders charts."""
    await page.goto(f"{BASE_URL}/settings/cost-dashboard")
    await page.wait_for_selector(".cost-dashboard-page", timeout=10000)

    # Summary cards should be visible
    stat_cards = page.locator(".stat-card")
    assert await stat_cards.count() >= 4

    # ECharts canvases should render
    charts = page.locator("canvas")
    count = await charts.count()
    assert count >= 1


async def test_cost_dashboard_period_toggle(page: Page) -> None:
    """Verify period selector toggles."""
    await page.goto(f"{BASE_URL}/settings/cost-dashboard")
    await page.wait_for_selector(".el-radio-group", timeout=10000)

    # Click 7-day option
    await page.click('text=最近7天')
    # Click 30-day option
    await page.click('text=最近30天')

    # Page should still be functional after toggle
    stat_cards = page.locator(".stat-card")
    assert await stat_cards.count() >= 4
