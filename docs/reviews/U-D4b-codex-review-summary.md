# U-D4b Codex Review Summary

**Task**: U-D4b — fix dry-run `snapshot_at` inversion (wall-clock `fetch_time` → T-1 EOD anchor) + rerun
**Date**: 2026-05-26
**Scope reviewed**: `scripts/dry_run_realdata.py`, `tests/test_dry_run_double_line.py`, `.gitignore`
**Gate**: codex `review --uncommitted` (cycle 1) + `exec --sandbox read-only` (verify)

## Context

Session #43 finished U-D4 (`0b6c2bc`); the owner then ran the real dry-run
(`python scripts/dry_run_double_line.py`, real Tushare + real qwen). The live
chain ran end-to-end (5522 codes / 30 trading days / 61 checksummed snapshots;
real qwen 4-agent debate; cost ¥0.0483 ≪ ¥20) but the verdict was **FAIL,
exit 1**: every `InstructionPlan` crashed with
`data_snapshot.snapshot_at must be strictly before created_at`
(`backend/models/instruction.py:230`).

**Root cause** — a dry-run *replay-fidelity* defect, not a production bug:
- The dry-run replays a *past* run day; `run_simulation` drives `created_at`
  from the replayed run-day 09:30.
- The live `Line1FrameAssembler` stamps `fetch_time_utc = datetime.now(UTC)`
  (`line1_frame.py:265` / `:352`), i.e. tonight's wall clock when assembled.
- `frame.fetch_time_utc` is the single source both lines feed into `snapshot_at`
  (Line-1 via `line1_context_provider.py:339`; Line-2 daily via
  `dry_run_double_line.py:412` → `Line2DailyProvider(snapshot_at=...)`).
- ⇒ `snapshot_at` (tonight) ≥ `created_at` (the replayed past 09:30) → crash.

In real production (run ~09:35 against a ~09:30 frame) `fetch < create`, so the
invariant holds; only the dry-run's replay of a past run day inverts it.

## Fix (dry-run script layer only — zero production change)

1. **`t_minus_1_eod_utc(as_of)`** — anchors the dry-run frame's
   `fetch_time_utc` to the T-1 EOD close (`as_of` 15:00 Asia/Shanghai → UTC),
   strictly before the replayed run-day 09:30 `created_at`. Injected into the
   assembler via `now_utc=` (the existing `Line1FrameAssembler.__init__` seam).
2. PIT contract preserved (R0 §3 red line A): `fetch_time_utc` is pure
   provenance — **not** in the snapshot checksum (computed over `raw_payload`
   only, `snapshot.py:105`) nor the replay digest, so bit-exact replay is
   unaffected. Production `Line1FrameAssembler` keeps its wall-clock default.

## Codex cycle 1 findings (both fixed)

| Sev | Finding | Resolution |
|-----|---------|------------|
| **P1** | Rerun against the persistent `data/dry_run/frames` store would **reuse** a pre-fix wall-clock-stamped derived frame verbatim (`fetch_time_utc` is not a `SnapshotStore` reuse key), so the injected clock would not take effect and the crash would persist. | Added `_frame_store_root()`: defaults to a **fresh** `tempfile.mkdtemp()` store when `QUANTMIND_DRYRUN_FRAME_ROOT` is unset → no stale reuse, the T-1 EOD clock always applies. Owner can pin the env var for cross-run reuse. Regression test asserts the fresh-store default. |
| **P2** | Untracked `data/dry_run/` generated state (frame store + artifacts) must not be committed — committing stale frames would make future dry-runs reuse them; also tens of MB of generated market data. | Added `data/dry_run/` to `.gitignore`; removed the stale `data/dry_run/frames/`. Confirmed nothing under `data/dry_run/` is staged/tracked. |

## Codex verify pass

> **No new P0/P1/P2 issues found.** P1 + P2 resolved.

Sanity checks confirmed: `t_minus_1_eod_utc(2026-05-15)` → `2026-05-15T07:00:00+00:00`
(15:00 CST, tz-aware); `backend/orchestration/line1_frame.py` diff empty (production
unchanged, still wall-clock default); PIT replay checksum still over `raw_payload`
only and replay digest uses snapshot id / row key / row bytes, not `fetch_time_utc`.

## Local gates (all green)

- `pytest -q --cov=backend --cov-fail-under=70` → **3828 passed / 13 skipped**, 90.42% (baseline 3824 → +4).
- `ruff check scripts/dry_run_realdata.py tests/test_dry_run_double_line.py` → clean.
- `bash scripts/redline-check.sh` → all pass (M-004 / X-018 / N-005 / L-004 / K-006 intact).

## Tests added (4)

- `test_t_minus_1_eod_utc_anchors_to_tminus1_close` — helper returns `as_of` 15:00 CST as tz-aware UTC.
- `test_t_minus_1_eod_utc_strictly_before_replayed_created_at` — anchor < replayed run-day 09:30 (the `instruction.py:230` invariant, covering the snapshot_at both lines feed).
- `test_assemble_real_frame_stamps_tminus1_eod_not_wallclock` — real assembler + store end-to-end (network faked) → frame stamped at T-1 EOD, < created_at.
- `test_assemble_real_frame_uses_fresh_store_when_root_unset` — P1 regression: fresh ephemeral store by default, never the persistent stale path.
