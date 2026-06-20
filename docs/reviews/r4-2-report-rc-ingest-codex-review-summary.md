# R4-2 report_rc ingest — codex review summary (2026-06-20)

**Task:** R4-2 — add `report_rc` (broker analyst forecasts/ratings, the round-4 analyst-revision
alpha source) to the PIT ingest layer:
- `backend/data/tushare_client.py::report_rc` — single `report_date` (cap-immune) OR paginated
  `start_date`/`end_date` range (single-call cap 5000 → `_fetch_paginated` page_limit 3000); `fields`
  pinned (incl. `create_time`) for byte-stable, replayable, backfill-guardable pulls.
- `scripts/factor_research/ingest_round2_data.py --phase round4` — `report_rc_month_ranges` (full
  calendar-month spans incl. weekends, final month capped at the locked calendar) + `ingest_report_rc`
  / `ingest_round4` (byte+checksum+idempotent; sparse STREAM → NO survivorship coverage manifest;
  integrity = in-client pagination-to-short-page + require_non_empty over verified-dense months).

**Local gates (pre-codex):** `mypy --strict scripts/factor_research` ✓ · ruff ✓ · redline ✓ ·
419 tests (factor_research + tushare_client + market-data) ✓ · `--phase round4 --dry-run` ✓
(150 month snapshots 2014-01 → 2026-06-12).

## codex `codex review --uncommitted`

### Cycle 1 — 1 × P1 (0 P0/P2/P3)
- **[P1] require_non_empty over unverified pre-history months** — `ingest_round2_data.py`.
  `REPORT_RC_FIRST_YEAR=2014` + `require_non_empty=True`: if any 2014 pre-history month were empty, a
  fresh `--phase round4` run would mark it FAILED and exit non-zero. The change had only verified
  2014-12 had data, not 2014-01..11 → unverified assumption.
- **Resolution (verified, NOT assumed):** real-probed every 2014 month (2026-06-20) → ALL dense
  (4146–12906 rows/month, no gaps); report_rc history runs back to ≤2010 (2013/2012/2011/2010 each
  8000–12000 rows/month). So the 2014 floor enumerates ONLY data-bearing months → `require_non_empty=True`
  stays a valid fail-closed corruption/truncation check that never wrongly fails a fresh run. The
  verification is now documented in the `REPORT_RC_FIRST_YEAR` comment + `ingest_report_rc` docstring
  (assumption → cited fact). No logic change needed (the code was already correct for the real data).

### Cycle 2 — CLEAN
> "The changes are covered by relevant tests, and I did not identify a discrete correctness issue in
> the modified or untracked files."

codex independently ran `mypy --strict backend/data/tushare_client.py` → *Success: no issues found*,
and `git diff --check` → clean.

## mypy note (root-caused, not papered over)
The R4-2 ingest forced mypy to fully analyse `TushareClient` (via the new `_Round2Client` Protocol
member + `main()` passing `TushareClient` to `ingest_round4`), which surfaced that **tushare 1.4.29
ships no `py.typed`** — a latent untyped-import that HEAD never analysed deeply. Verified via `git stash`
+ `--no-incremental` that HEAD is clean and the trigger is the ingest change (not tushare_client alone).
Fixed with the project's established untyped-third-party-dep pattern:
`[[tool.mypy.overrides]] module=["tushare"] ignore_missing_imports=true` (mirrors the existing scipy /
rqalpha overrides) + `cast(TusharePro, ts.pro_api(token))` at the isolated `_build_pro` boundary.

## Verdict
R4-2 code is **codex-clean** (1 P1 found cycle 1 → resolved → cycle 2 clean). All local gates green.
**The real ingest is owner-gated (「开」)** — no network ingest has run; only read-only probes (¥0).
