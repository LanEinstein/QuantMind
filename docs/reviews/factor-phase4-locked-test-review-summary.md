# Code review — Phase 4 locked-test harness (the test-touching path)

> Task: `build_factor_panel.build_test_panel` (the SOLE sanctioned reader of the
> sacred test window) + `phase4_locked_test.py` (one-shot runner) + the
> horizon-exact benchmark fix in `portfolio_backtest.py`. Reviewed **2026-06-16**
> via two focused adversarial agents (codex at usage limit until 2026-06-18):
> (1) a look-ahead / data-leakage audit, (2) a verdict-correctness + pre-commitment
> audit. This code performs an IRREVERSIBLE one-shot on a held-out test set, so
> the review bar was "prove no leak", not just "find bugs".

## Leakage audit — verified CLEAN (would otherwise burn the test set)
- **Feature look-ahead:** `_build_rows` passes `cs.adj_close[:pos+1]` where
  `pos` is date *d*'s index in that code's ascending series; `feature_dates =
  buffer + test_dates` is strictly ascending, so `[:pos+1]` contains only bars
  ≤ *d*. No future bar reachable for any factor/filter (`circ_mv[pos]`,
  `amount[pos+1-20:pos+1]`, `raw_close[pos]` all ≤ pos).
- **Labels:** `_forward_returns` reads `adj_close[pos+h]` guarded by
  `nxt < len`; the last rebalance dates correctly yield `None` (dropped), never
  reuse a stale bar. Labels are strictly bars > *d*.
- **Buffer:** `pre_test[-30:]` = 10 train_val + 20 embargo dates — zero test
  dates (no covenant breach), calendar-contiguous with test_start (the lock's
  contiguity assertion guarantees no gap), and 30 ≥ 21 so the first test
  rebalance date has a full 20-day factor + liquidity window.
- **No read beyond test_end:** the series ends at the last test date; benchmark
  is date-keyed lookup only.

## Verdict + firewall — verified CORRECT
- Four criteria map to the right fields/inequalities; `max_drawdown` is a
  positive fraction so `<= 0.15` is the right direction; `passed = all(...)`.
- `load_frozen_weights` fails closed on factor-order drift and on any weight
  > 5e-4 from the 3-dp git pre-commitment — the one-shot can only ever score the
  strategy frozen before test was touched. Tolerance verified safe (admits
  full-vs-3dp rounding, rejects any economically different weight).
- Empty/degenerate test panel → `total_return=0 → net_positive False → FAIL`
  (honest, no crash; summary path guarded against div-by-zero).

## Fix applied — benchmark horizon alignment (both agents)
The CSI300 leg was measured rebalance-date→next-rebalance-date with a `0.0` on
the final period, while the strategy leg is exactly `fwd_ret_5d`. Not a
look-ahead leak, but it feeds `excess_vs_bench` (the `beats_csi300 >= 0`
criterion) and could shift it by ~one period in a slightly PASS-favouring
direction. **Fixed:** new `_benchmark_leg` measures CSI300 over exactly
`horizon` trading bars on the benchmark's own calendar for every rebalance
(including the last). Re-running the search confirmed the selected weights and
DSR/PBO/SPA are unchanged (all benchmark-independent); only the displayed val
excess refined +70.77% → +69.68% (the previously-uncounted final benchmark leg),
validating the fix. Unit test `test_benchmark_leg_is_horizon_exact_including_last_period`
added.

## Noted, not changed
- `annual_return` is reported but is NOT a PASS criterion (display only).
- Sharpe is annualised (ppy = 252/horizon ≈ 50.4); the "Sharpe ≥ 0.5" bar is on
  the annualised figure (standard reading, consistent with the search's Sharpe).
  Flagged for owner awareness: a 0.5 *annualised* bar is materially easier than
  0.5 per-period.

86 factor_research tests green, ruff + mypy strict clean. Test-panel mechanics
verified on synthetic NON-sacred data before any real test read.
