# Phase 5 Readiness — Medium-severity backlog

Findings surfaced during the 5-round /codex-review gate that were tagged MEDIUM and explicitly deferred per `feedback_codex_review_gate.md` ("MEDIUM findings go to docs/reviews/<task-id>-backlog.md with owner + deadline").

## R4 — Performance & a11y MEDIUM (deferred)

### R4-M1: Catch-up startup issues one Mongo query per watchlist stock

- **File**: `backend/data/analysis_scheduler.py` around `_compute_catch_up_targets`
- **Finding**: For a watchlist capped at 500 stocks, startup can fire hundreds of `query_signals(...)` round-trips for a single date check.
- **Proposed fix**: Fetch today's signals once with `{"trade_date": d, "stock_code": {"$in": codes}}`, build a `set()`, diff against the watchlist.
- **Owner**: LanEinstein
- **Deadline**: before week 2 of evaluation (2026-05-02)

## R3 — Testing coverage MEDIUM

### R3-M1: Playwright selectors are implementation-specific

- **File**: `frontend/e2e/agent-debate.spec.ts` (mainly around lines 120, 206, 207)
- **Finding**: Heavy reliance on CSS class selectors (`.debate-layout`, `.history-item`, `.stock-selector`) and Element Plus internal classes (`.el-select-dropdown__item`). A harmless markup/style refactor could break the suite even when behavior is correct.
- **Proposed fix**: Add stable `data-testid` hooks to the key app-level controls (stock selector, start button, history list + items, debate panels) and prefer `getByRole/getByLabel/getByTestId` in the tests. Keep the Element Plus dropdown selector only when it is the only reliable handle; document why.
- **Owner**: LanEinstein
- **Deadline**: before the 4-week evaluation window closes (2026-05-23)
