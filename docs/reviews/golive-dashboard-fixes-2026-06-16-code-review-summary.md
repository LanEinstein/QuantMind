# Code Review Summary — Go-Live Dashboard Fixes (2026-06-16)

**Scope:** working-tree fixes found by the pre-open Playwright front-back
integration exam (`frontend/scripts/live-integration-exam.mjs`). Reviewed via
`/code-review high` (codex CLI rate-limited until 06-18 → fallback per
[[feedback_codex_rate_limit_fallback]]).

## Changes reviewed (5 files + 1 new harness)

| File | Fix |
|------|-----|
| `backend/data/database.py` | `query_news` projects Mongo `_id`(ObjectId)→string `id` (immutable new dicts) — fixes `/api/news/latest` 500 `PydanticSerializationError`. |
| `backend/api/market.py` | `get_sectors` fails OPEN (empty list + `log.warning`) on `DataFetchError` instead of `_err()`→500 — akshare `RemoteDisconnected` is an infra glitch (CLAUDE.md §3). |
| `frontend/src/views/settings/LLMRouter.vue` | Register echarts `GraphChart`+Tooltip+Legend+CanvasRenderer via `use([...])` — fixes `Renderer 'undefined'`. |
| `frontend/src/views/settings/CostDashboard.vue` | Register echarts `BarChart`/`PieChart`+Grid+Tooltip+Legend+CanvasRenderer. |
| `frontend/src/components/charts/SectorHeatmap.vue` | Harden both tooltip/label formatters against null/undefined `changePct`. |
| `frontend/scripts/live-integration-exam.mjs` (new) | Read-only live front-back exam harness (no route mocking; complements `ad-playwright-exam.mjs`). |

## Angles checked

- **Correctness:** `doc["_id"]` always present in Mongo `find()`; `get_sectors`
  still 500s on non-`DataFetchError` (fail-closed for the unexpected); echarts
  modules registered match each view's chart types; `Number(x ?? 0)` handles
  null/undefined.
- **Removed behavior:** the sectors 500 path was intentionally replaced by
  fail-open; no consumer depended on it.
- **Cross-file:** `query_news` has 2 callers (`market.py` `get_latest_news` /
  `get_stock_news`), neither reads `_id`; no backend consumer reads news `_id`;
  `NewsFeed.vue` keys on `:key="idx"`. Safe.
- **Conventions:** immutability (new dicts) ✅; fail-open for infra glitches ✅;
  no red line touched (read-only GET + display only; no new write endpoint, no
  `backend.risk` import, no LLM in data path, 127.0.0.1 unchanged).

## Verification

- Live exam post-restart: 17 pages, **0 failed API / 0 console errors / 0 page
  errors**, `/portfolio` WS connected, 128 real `/api` calls.
- `ruff` clean; `pytest -k "market or news or sector or database"` → 449 passed;
  `mypy` database.py 22→21 errors (net **−1** `no-any-return`; remainder
  pre-existing motor `Any`-returns).

## Result

**No P0/P1/P2 findings.** Cleared for commit. Push held for owner authorization.
