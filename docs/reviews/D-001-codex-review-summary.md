# D-001 RiskEngine 14-check + P0-7 RiskConfig — Codex Review Summary

**Task**: D-001 Phase D 风控与路由 — RiskEngine 7-check → 14-check expansion + P0-7 locked RiskConfig thresholds.
**Session**: 2026-05-12 session #10.
**Review cycles**: 5 (R1–R5). All resolved.

## Cycle breakdown

| # | Mode | Verdict | Issues |
|---|------|---------|--------|
| 1 | `review --uncommitted` | NEEDS_FIXES | 3 P1 + 2 P2 + 1 P3 |
| 2 | `review --uncommitted` (post-cycle-1 fixes) | NEEDS_FIXES | 1 P1 + 2 P2 |
| 3 | `review --uncommitted` (post-cycle-2 fixes) | NEEDS_FIXES | 2 P2 |
| 4 | `review --uncommitted` (post-cycle-3 fixes) | NEEDS_FIXES | 3 P2 + 2 P3 |
| 5 | `review --uncommitted` (post-cycle-4 fixes) | **PASS** | 0 issues |

> Cycle 2 first attempt via `codex exec` timed out at 1000s; retried via `review --uncommitted` which succeeded. Cycles 1, 3, 4 used `review --uncommitted` directly.

## Cycle 1 findings (3 P1 + 2 P2 + 1 P3)

| # | Severity | File | Issue | Resolution |
|---|----------|------|-------|------------|
| 1 | P1 | `backend/risk/engine.py` | Caller could pass stale `stock_meta` for a different code; engine treated mismatched metadata as valid. | Added pre-check at `validate_order` entry: rejects with `rule_name="stock_meta_mismatch"` when `stock_meta.code != order.code`. |
| 2 | P1 | `backend/broker/models.py` | `price_limit_pct_by_board: dict` was mutable through per-key assignment (`cfg.universe.price_limit_pct_by_board["sh_main"] = 0.99`) despite `frozen=True`. | Added `@model_validator(mode="after")` that wraps the dict in `MappingProxyType`. |
| 3 | P1 | `backend/risk/engine.py` | Limit-up/down comparison used raw float multiplication + 0.001 tolerance — missed exchange-rounded boundaries (e.g. `1.65 * 0.9 = 1.485` → 1.49 published, but float gives 1.4849999 → naive `round` returns 1.48). | New helper `_exchange_price_limit` does Decimal arithmetic (str → Decimal multiply → quantize ROUND_HALF_UP) matching 四舍五入 exchange convention. Removed tolerance; comparisons are now plain `>=` / `<=`. |
| 4 | P2 | `backend/risk/engine.py` | Check 13 used strict `<` threshold while `CircuitBreaker._should_halt` uses `<=`. Inconsistent at exact -5%. | Changed to inclusive `<=`. |
| 5 | P2 | `backend/api/risk.py` | `/api/risk/radar` + `/api/risk/config` returned hard-coded `total_position_limit=80` instead of the locked 70%. | Read from `risk_config.position_limits.max_total_position_pct`; tightened static fallbacks to 70/15/5. |
| 6 | P3 | `scripts/redline-check.sh` | Scanned non-existent dir `backend/api/risk/` instead of file `backend/api/risk.py` — guard was a no-op. | Scan `backend/api/risk.py` + `backend/api/risk_*.py` glob. |

## Cycle 2 findings (1 P1 + 2 P2)

| # | Severity | File | Issue | Resolution |
|---|----------|------|-------|------------|
| 1 | P1 | `backend/risk/engine.py` | NaN/Inf `current_price` or `prev_close` (pandas/akshare can surface as NaN) silently passed comparison checks → fail-open. | Added `math.isfinite()` guards in checks 2 and 12; treat non-finite as missing data → fail-closed. |
| 2 | P2 | `backend/broker/models.py` | `MappingProxyType` breaks Pydantic v2 `model_dump(mode="json")` / `model_dump_json()`. | Added `@field_serializer("price_limit_pct_by_board")` that converts to a plain dict on serialization while keeping in-memory proxy immutable. |
| 3 | P2 | `scripts/redline-check.sh` | YAML lock guard didn't check `universe.price_limit_pct_by_board`; a silent edit (`sh_main: 0.99`) would weaken check 2/12 without scanner failure. | Added 4-key dict equality check against the locked table. |

## Cycle 3 findings (2 P2)

| # | Severity | File | Issue | Resolution |
|---|----------|------|-------|------------|
| 1 | P2 | `backend/api/trading.py` + frontend | 15% single-stock limit not propagated to consumers: API fallback still 0.20; position drawer/table colored at `pct > 0.20`; drawer label `/ 20%`. | Updated API fallback to 0.15; frontend gauges trip red at `> 0.15` and yellow at `> 0.10`; label changed to `/ 15%`; `mockRadarData` aligned with P0-7 locks. |
| 2 | P2 | `scripts/redline-check.sh` | grep-based scan missed parenthesised multiline imports + `StopLossConfig` single-line imports. | Replaced with Python `ast`-based scanner walking every `ImportFrom`/`Import` node; folds dotted/relative/parenthesised forms; full name set (`StopLossConfig` included). |

## Cycle 4 findings (3 P2 + 2 P3)

| # | Severity | File | Issue | Resolution |
|---|----------|------|-------|------------|
| 1 | P2 | `backend/risk/engine.py` | Check 2 used raw deviation %, but exchange-rounded limit can exceed raw % (e.g. prev_close=1.65, 10% board → upper=1.82 = 10.3% raw). A SELL at 1.82 was wrongly rejected as "too far". | When `stock_meta` is present, check 2 compares against `_exchange_price_limit()` upper/lower bounds directly (board-aware). Global fallback path unchanged. |
| 2 | P2 | `backend/risk/engine.py` | NaN/Inf `today_portfolio_pnl_pct` silently passed strict `<=` comparison → fail-open on missing day-open NAV. | Added `math.isfinite()` guard at check 13 entry; non-finite → fail-closed. |
| 3 | P2 | `scripts/redline-check.sh` | Submodule-import-handling block had dead code (`pass` after an always-False `not in` comparison); `from backend.risk.engine import RiskConfig` slipped past. | Refactored AST scanner with explicit `_is_restricted_module` prefix matching + dedup set. |
| 4 | P3 | `frontend/.../PositionDetailDrawer.vue` | Progress-bar scale `* 500` was calibrated for 0.20=100%; new 15% cap rendered as only 75% full. | Changed to `(position_pct / 0.15) * 100`. |
| 5 | P3 | `frontend/.../stores/risk.ts` | `mockRiskConfig()` dev fallback still returned obsolete 20%/80%/-3% values. | Updated to 15/70/-5 matching P0-7 lock. |

## Cycle 5 verdict

> No discrete blocking issues were found in the changed or untracked files. The risk-engine and frontend type checks/tests reviewed appear consistent with the intended 14-check expansion.

## Final gate snapshot

- pytest: 1641 passed, 11 skipped (no failures)
- risk coverage: 98.15% (≥ 95% required)
- overall coverage: 86.57% (≥ 70% required)
- ruff (touched files): clean
- frontend type-check: clean
- frontend tests: 80 passed
- redline-check.sh: all green (including new P0-7 RiskConfig immutability section)

## Files touched

```
M backend/api/risk.py
M backend/api/trading.py
M backend/broker/models.py
M backend/risk/__init__.py
M backend/risk/engine.py
M config/risk.yaml
M docs/plan.html
M scripts/redline-check.sh
M tests/test_risk_engine.py
M tests/test_risk_isolation_redline.py
M frontend/src/components/trading/PositionDetailDrawer.vue
M frontend/src/components/trading/PositionTable.vue
M frontend/src/stores/risk.ts
?? backend/risk/daily_state.py
?? backend/risk/stock_meta.py
?? tests/test_risk_data_types.py
?? docs/reviews/D-001-codex-review-summary.md
```
