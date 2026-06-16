# Code review — Sobol weight search + honest selection (Phase 3)

> Task: `scripts/factor_research/weight_search.py` (new) + `portfolio_backtest.py`
> changes (net_returns field, orient override, groupby hoist). Reviewed
> **2026-06-16** via `/code-review high` (Claude multi-agent — codex CLI at usage
> limit until 2026-06-18, per the documented fallback). 8 finder angles
> (3 correctness + reuse/simplification/efficiency + altitude + conventions),
> recall-biased. Local gate green throughout: 71 factor_research tests pass,
> ruff clean, mypy strict clean.

## Findings & resolutions

| # | Sev | Finding | Resolution |
|---|-----|---------|------------|
| 1 | HIGH | `disclose()` was fed only the 16 train-robust finalists, not the full 512-candidate pool — violating its documented contract ("the full pool — never just the survivors") and biasing PBO/SPA favourably. The module's entire purpose is honesty. | **Fixed.** `search()` now runs the val pass over **all N candidates**; `_disclosure` feeds the full N×periods matrix to PBO-CSCV and SPA. Selection still uses the train-robust top-K → val-best discipline. PBO moved 0.194→0.127 (honest full-pool number). |
| 2 | MED | `minbtl_admits` is structurally always `False` on a weekly panel: `minimum_backtest_length` is daily-calibrated (periods_per_year=252) while observations are 5-day periods — a units mismatch that could mislead a reader into seeing a strategy-specific rejection. | **Documented.** `_disclosure` docstring + the runtime NOTE state MinBTL is a conservative daily-calibrated floor a ~2.3y weekly val rarely clears; DSR/PBO/SPA are the primary disclosures and the locked test set is the verdict. (Directionally honest: ~2.3y *is* short for 512 trials; not papered over.) |
| F2 | MED | `groupby` was hoisted out of the per-period loop but still rebuilt ~530× over the two unchanging panels. | **Fixed.** Added `group_by_date()` + optional `groups=` param to `backtest`; `_run_pass` groups each panel once and shares it across all candidates. Made the now-doubled (full-pool val) backtest count affordable (~6 min). |
| F1 | MED | `simplex_sobol` re-inlined the Kraemer sorted-spacings map that already exists as `quant_param_search._kraemer_simplex` (import-allowed sister module). | **Fixed.** `simplex_sobol` now calls the shared `_kraemer_simplex` — one implementation. |
| C1 | MED | `search()` was ~70 statement lines, exceeding the CLAUDE.md §3 "<50 lines" rule. | **Fixed.** Extracted `_run_pass`, `_select`, `_disclosure`, `_assemble_result`; `search()` is now ~30 lines of orchestration. |
| C2 | MED | Sobol weight vectors map to factors positionally; a silent reorder of `factor_lib.FACTORS` would remap every searched weight with no error. | **Fixed.** Pinned `EXPECTED_FACTOR_ORDER`; `search()` fails closed on drift (mirrors locked_split's dates_sha256). The factor order is also recorded in the result JSON (`factor_names`) so the artifact is self-describing. Test added. |
| 3 | LOW | The reported `train` summary re-ran a separate backtest of the selected weights (benchmark-only difference; Sharpe is benchmark-independent so selection unaffected). | **Fixed incidentally** — the train pass now carries the benchmark, so the reported `train` summary reads from `train_results[selected]`; the redundant re-run is gone. |
| F4 | — | Suggested replacing the `orient` override with `ret_20d=-1.0`. | **Refuted / kept `orient`.** A negative weight hits the `w > 0` filter in `backtest` (factor excluded from `dropna` yet scored) — inconsistent. `orient` is the clean, single-factor, dropna-consistent construction; the altitude finder independently rated it "clean generalization, not a hack." |
| F3 | — | `_summary` hand-lists 9 scalar fields instead of deriving from `asdict`. | **Kept explicit.** A stable, intentional JSON schema; explicit is clearer than `asdict`-minus-tuples and unaffected by future array fields. |

## Cleared on inspection (no change)
- Sacred-split guard: `assert_all_not_test` runs unconditionally on all panel
  dates, reachable even when `split` is injected. Nothing reads test dates.
- Purge (4 rebalances ≈ 20td at 5td cadence) covers the 20d max forward label;
  no train label leaks across the cutoff into a kept val date.
- groupby refactor preserves determinism (within-group order retained, then
  deterministic re-sort by `[_score, code]`); same date set as the old mask.
- Momentum incumbent `orient={"ret_20d": True}` correctly scores HIGH ret_20d.
- Import isolation: only `scipy`/`pandas`/sibling `factor_research`/
  `backend.strategy_evolution`; no `backend.{llm,agents,mirofish}`. LLM-free.
