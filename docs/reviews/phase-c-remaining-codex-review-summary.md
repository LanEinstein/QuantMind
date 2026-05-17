# Phase C Remaining (C-005 + C-006) Codex Review Summary

**Date**: 2026-05-17
**Scope**: 16 files / 1486 insertions / 138 deletions across C-005 (multi-domain
news + within-domain dedupe) and C-006 (MiroFish evidence wiring).
**Result**: PASS after 4 fix cycles + 1 final read-only verification.

## Pre-codex local gate

* `pytest -q --cov=backend --cov-fail-under=70` — 2375 passed / 11 skipped /
  88.59% overall coverage (baseline 2309 → +66 new tests across this work).
* `pytest backend/risk --cov-fail-under=95` — 98.15% risk coverage (unchanged).
* `ruff check` on the 16 touched files — clean.
* `scripts/redline-check.sh` — all green (including the data-quality boundary
  and P0-8 evidence_id prefix allowlist checks).
* Frontend `vue-tsc` + `vitest --run` + `npm run build` — 120 vitest passed
  (no frontend touched this phase).

## Cycle-by-cycle findings + fixes

### Cycle 1 (`codex review --uncommitted`): 2 P1 + 2 P2 — all genuine, all RESOLVED

| # | Severity | File | Issue | Fix |
|---|---|---|---|---|
| 1 | P1 | `backend/data/news_crawler.py` | `fetch_latest_news` defaulted `include_cctv=False`, no 6h CCTV cron registered → political domain inactive at runtime | Initially registered a 6h interval cron; cycle 4 P3 re-locked to 09:00 / 15:30 / 20:00 Asia/Shanghai per P0-8 §2 redline 13. |
| 2 | P1 | `backend/data/scheduler.py` | `MiroFishEvidenceWriter` never constructed in `main.py` → EOD cron + intelligence_officer event path effectively inactive | `_init_data_layer` builds the writer from `MongoDBService`, passes to `DataScheduler(mirofish_writer=…)`, stores on `app.state.mirofish_writer`. |
| 3 | P2 | `backend/agents/intelligence_officer.py` | `zip(events, results)` shifted indices after a simulation raised → HIGH event could be paired with the wrong result | Build `event_results: list[tuple[Event,Result]]` only on successful pairs; downstream iterates this list. Also fixed structlog kwarg collision `event=event.title` → `event_title=event.title`. |
| 4 | P2 | `backend/data/news_dedupe.py` | Cross-domain duplicate URLs preserved in memory but `save_news` upsert-by-url-only would still collapse them in Mongo | `news_articles` unique index → `(url, domain)`; `save_news` upserts on `{url, domain}`. |

### Cycle 2 (`codex exec`): 2 P1 + 2 P2 — all genuine, all RESOLVED

| # | Severity | File | Issue | Fix |
|---|---|---|---|---|
| 5 | P1 | `backend/main.py` | `AnalysisServices` still missing `MiroFishSimulator`; the intelligence_officer event-driven path is gated on `mirofish_simulator is not None` so it never fired in production | `_init_analysis_scheduler` constructs `MiroFishSimulator(application.state.llm_router)` and passes it to `AnalysisServices(mirofish_simulator=…)`. Failure is fail-open `None` with a warning log. |
| 6 | P1 | `backend/agents/intelligence_officer.py` | Legacy `simulations` collection insertion violates the "output only to evidence_collection" P0-8 §2 redline 14, even though it was framed as browser-only | Removed the entire `simulations` write block; the MIROFISH-prefixed evidence_collection write is the single MiroFish persistence path. |
| 7 | P2 | `backend/data/database.py` | `(url, domain)` index lacked a migration; existing deployments retain unique `url_1` index | `initialize()` now drops legacy `url_1` (try/except), backfills missing `domain="financial"` on legacy rows, then creates the compound unique. |
| 8 | P2 | `backend/mirofish/output_writer.py` | `count_documents + insert_one` is non-atomic; two concurrent event writers could both bypass the cap=1 check | Added unique *partial* index on `(trade_date, path)` filtered to `path='event_driven'`. Writer translates `DuplicateKeyError` (Mongo code 11000/11001 or "duplicate key" message) into `MiroFishEvidenceError(reason='daily_cap_reached')`. |

### Cycle 3 (`codex exec` re-verification): 8/8 prior RESOLVED + 2 new P2 — all RESOLVED

| # | Severity | File | Issue | Fix |
|---|---|---|---|---|
| 9 | P2 | `backend/data/database.py` | `drop_index("url_1")` swallowed *every* exception silently; a leftover legacy unique index would still collapse cross-domain duplicates | Narrowed except to `OperationFailure` with codes 26/27/28 (NamespaceNotFound / IndexNotFound / NamespaceExists); other failures re-raise so a stuck legacy index surfaces. |
| 10 | P2 | `backend/main.py` | If the partial cap index creation failed, the writer was still wired and silently degraded to non-atomic count-based cap | `MongoDBService.evidence_event_cap_index_ok` flag set to `True` only after successful index creation; writer checks the flag and raises `MiroFishEvidenceError(reason='cap_index_missing')` on event_driven writes when missing. EOD path unaffected (uncapped). |

### Cycle 4 (`codex exec` re-verification): 10/10 prior RESOLVED + 1 P2 + 1 P3 — all RESOLVED

| # | Severity | File | Issue | Fix |
|---|---|---|---|---|
| 11 | P2 | `backend/data/news_dedupe.py` | Used `(domain, url)` keys; P0-8 §1.3.4 / §2 redline 16 actually lock dedupe to `domain + title + 60s window`. The redline is "保留最早" not URL collapse | Rewrote dedupe: key on `(domain, normalised_title)` and reject articles within `TITLE_DEDUPE_WINDOW_SECONDS=60` of an already-kept article in the same bucket. Exposed locked constant for redline-check.sh and tests. |
| 12 | P3 | `backend/data/scheduler.py` | CCTV scheduled as `interval=6h` from process start; P0-8 §2 redline 13 locks `09:00 / 15:30 / 20:00 Asia/Shanghai` — restarts would shift the political-domain pull window away from the 19:30 broadcast | Removed the 6h interval; registered 3 discrete cron jobs `cctv_news_job_{0900,1530,2000}` each pinned to the locked Asia/Shanghai checkpoint. |

### Final read-only verification (cycle 5)

12/12 RESOLVED, 0 critical regressions, verdict **PASS**.

> Quote: "all 12 prior issues verify as resolved, MiroFish writes are
> constrained to `evidence_collection`, and the `event_driven` cap is
> fail-closed when the partial unique index is absent."

## Regression tests added (66 new pytest, 0 frontend)

* `tests/test_news_dedupe.py` — 17 tests (locked window constant, inside / outside
  window, cross-domain preservation, normalisation, distinct titles, override,
  order, empty input, determinism, source→domain map, histogram, split).
* `tests/test_news_crawler.py` — 32 tests (multi-domain fan-in, CCTV opt-in,
  one-source-failure isolation, within-domain dedupe through service, per-source
  parsers, fallback URL synthesis, safe-fetch wrappers, import isolation lint).
* `tests/test_mirofish_output_writer.py` — 22 tests (severity threshold,
  evidence_id builders + pattern conformity, prefix guard, event-driven cap +
  race + per-trade_date reset, EOD uncapped, cap-index-missing fail-closed,
  module isolation lint).
* `tests/test_mirofish_integration.py` — +4 tests (writer called for HIGH event,
  cap rejection doesn't break pipeline, low-severity skips writer, pairing-after-
  failure regression).
* `tests/test_scheduler.py` — +9 tests (CCTV 3-checkpoint registration, CCTV
  fetch+persist, CCTV exception swallowed, MiroFish EOD cron registered when
  writer present, cron absent when writer None, EOD writes evidence, no-writer
  is no-op, EOD swallows writer failure).
* `tests/test_database.py` — +1 test (upsert key includes both `url` + `domain`).

## Lessons reinforced

This phase re-validated [[feedback_codex_findings_real]] once again. A run that
was pytest 2366 + ruff + redline + frontend all-green pre-codex turned up 12
real defects across 4 cycles — none false positives:

* 2 dead-on-arrival wirings (CCTV cron + MiroFish writer in `main.py`)
* 2 dead-code branches in production (no `MiroFishSimulator` injection + an
  un-wired event-driven evidence path)
* 1 redline-strict redirection of MiroFish output (removing the legacy
  `simulations` second sink)
* 3 silent-degradation paths around the event_driven cap (non-atomic count
  check, missing partial index, over-swallowed drop_index error)
* 2 spec-vs-code drift findings (dedupe by URL instead of locked title+60s,
  CCTV interval cron instead of locked 09:00 / 15:30 / 20:00 cron)
* 1 latent structlog bug (`event=event.title` kwarg collision the new
  regression test happened to expose)
* 1 cross-domain Mongo persistence collapse (unique-url index)

Final gate: pytest 2380 passed / 11 skipped / 88.59% / risk 98.15% / ruff
touched-files clean / scripts/redline-check.sh all green.

Codex review reports: `docs/reviews/phase-c-remaining-codex-review-summary.md`.
