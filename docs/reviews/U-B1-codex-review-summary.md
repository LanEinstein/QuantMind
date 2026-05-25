# U-B1 Codex Review Summary — Line-1 frame assembler (Tushare → screener CSV, PIT raw + derived)

**Task**: U-B1 — `tushare_client.stock_basic()` + `backend/orchestration/line1_frame.py`
**Date**: 2026-05-25
**Cycles**: 1 review (`codex review --uncommitted`) + 1 read-only verify (`codex exec --sandbox read-only`)

## Cycle 1 — findings (2 × P2, both fixed)

- **[P2] Fail closed on an empty daily pull** (`line1_frame.py`): an empty/columnless
  `daily` frame for one trade_date was silently compressed out of every series; with the
  30-day window the screener still had 29 bars > `MIN_HISTORY_BARS` (21) and could emit
  candidates from a **partial** full-market pull.
  **Fix**: added `Line1FrameError` + `_validate_pull(endpoint, trade_date, df, required_cols)`
  invoked in `_fetch_or_load` on **both** the fetch path (before `store.put`, so a bad pull
  is never persisted) and the reuse path. `daily` requires `{ts_code, close, amount}`,
  `stock_basic` requires `{ts_code, name, list_date}`; `daily_basic` is unvalidated (not
  consumed). Empty/missing-column ⇒ fail-closed.

- **[P2] Rebuild derived frames when inputs change** (`line1_frame.py`): `_store_derived_frame`
  early-returned the existing derived snapshot keyed only by `trade_date`, so a changed input
  (raw restatement or a different `history_days`) exposed new `raw_snapshot_ids` while the
  derived bytes + `parent_snapshot_ids` still described the old inputs — stale data under
  false lineage.
  **Fix**: reuse only when `existing.raw_payload == frame_bytes` **and**
  `existing.metadata["parent_snapshot_ids"]` matches the freshly-computed parents; otherwise
  persist a **new version** (`existing.version + 1`) with the new bytes + lineage.

## Cycle 2 — read-only verify

**Verdict: PASS. No new P0/P1/P2.** Confirmed: reuse-path validation fails closed on a
previously-stored degraded payload (header-only ⇒ `Line1FrameError`; columnless bytes ⇒
pandas `EmptyDataError`, still before frame build); version-bump cannot collide with the
store's `(vendor, endpoint, trade_date, version)` guard sequentially (`latest()` reloads,
new version = latest + 1).

Non-blocking note: concurrent overlapping Line-1 assemblers for the **same** `as_of` could
race to `SnapshotOverwriteError` — fail-closed, not corruption. Production runs Line-1 once
per day, single-instance (scheduler process-wide lock), so this cannot occur; tracked as a
constraint, not a defect.

## Gate

10 new tests (assembler), module coverage 91%, `ruff` clean, `redline-check.sh` green,
full suite **3604 passed / 11 skipped** (baseline 3594 → +10).
