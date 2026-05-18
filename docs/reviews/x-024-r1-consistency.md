# X-024 Codex R1 — Consistency + Redline Coherence

**Date**: 2026-05-18
**Session**: #26 phase-x-E (Codex 5-round R1-R5, X-024..X-028)
**Round**: R1 / 5
**Codex CLI**: v0.130.0
**Model**: gpt-5.5 (codex default)
**Sandbox**: read-only

## Acceptance (per docs/plan.html#X-024)

> 0 critical 一致性冲突;PASS 才进 R2。

**Final verdict: PASS** (after 1 fix cycle).

## Scope

Verify the Phase X (P2-2 self-evolution) implementation is internally
consistent with:

- The 15 hard invariants from P2-2 §2 redlines 1~23 (esp. R1 / R3 / R7 /
  R21 / R22 / R23) and CLAUDE.md §2.
- The 3 P2-2-derived amendments (P0-7 / P1-2.A / P1-6 third).
- The pre-existing 130+ redlines that Phase X must not break.

The review was performed in `codex exec -s read-only` mode with a tight
prompt that enumerated 15 specific claims and forced codex to verify
each with a single file:line citation (≤ 20 grep/read commands), so
the synthesis fits inside one context window.

## Method — 15 enumerated claims

| # | Claim |
|---|-------|
| 1 | `backend/services/dspy_gepa_runner.py` rejects > 100 samples with a typed error (R1) |
| 2 | `backend/services/dspy_gepa_runner.py` rejects > `MAX_ITER` per-call iterations with a typed error (R1) |
| 3 | `backend/evolution/rag_ingester.py` rejects ingest when precision < 0.80 fail-closed (R3) |
| 4 | `backend/services/amendment_drafter.py` raises typed error when amendment is missing any of 4 mandatory sections (R7) |
| 5 | `backend/services/amendment_drafter.py` raises typed error when amendment has > 4 sections (R7 strict) |
| 6 | `backend/evolution/crawlers/spotlighting.py` datamarking applied to every body via `CrawlerBase._build_document` (R23) |
| 7 | `backend/evolution/rag_ingester.py` sanitiser COUNTS injection markers BEFORE HTML strip (session #23 P2-2 regression lock) |
| 8 | `backend/services/evolution_feishu_notifier.py` does NOT reference `FEISHU_CUSTOM_BOT_*` (P0-2-amendment-2026-05-16) |
| 9 | `backend/services/evolution_audit_writer.py` enforces actor ∈ {`SYSTEM`,`SCHEDULER`} for Category-5 events incl. `FAILURE`/`DEGRADED`/`BLOCKED` outcomes (P1-6-amendment-2026-05-11) |
| 10 | `backend/broker/scheduler.py` registers 5 cron jobs incl. `evolution_shadow_run` 22:00 mon-fri Asia/Shanghai (P1-2.A-amendment-2026-05-11) |
| 11 | Every Phase X LLM out-call (GEPA / FrontierCrawler.summariser / AmendmentDrafter) calls `cost_guard.assert_budget_allows` BEFORE the LLM call (P1-7) |
| 12 | No Phase X module imports `backend.{api,broker,risk,llm,agents,mirofish,data}` (P2-2 §2 redline 17) |
| 13 | `backend/api/evolution.py` exposes exactly 3 endpoints, all `GET` (P1-5 §2 redline 1+2) |
| 14 | `PromptRegistry` has no hot-reload path — version swap requires restart (P0-7 §2 redline 14 + P0-10) |
| 15 | `RagProvenanceEntry` is Pydantic v2 frozen + strict + `extra='forbid'` (P0-3 §2 redline 12) |

## Cycle 1 — initial review (3 VIOLATES found)

Tokens used: 46,578. Codex verdict: **MAJOR_CONCERNS**.

### Claim 5 VIOLATES — `_validate_sections` did not reject surplus sections

```text
File: backend/services/amendment_drafter.py:385
Codex evidence: `for section in MANDATORY_SECTIONS:` — validator only
checks missing mandatory sections; no surplus-section guard.
Severity: MEDIUM (defense-in-depth; in-tree drafter always builds 4)
```

### Claim 8 VIOLATES — docstring referenced `FEISHU_CUSTOM_BOT_*` literally

```text
File: backend/services/evolution_feishu_notifier.py:17
Codex evidence: `* Route is the self-built-app OpenAPI — :data:`FEISHU_CUSTOM_BOT_*`...`
Severity: LOW (docstring contrast doc; redline-check.sh already PASS because
the §5 grep targets WEBHOOK_URL|SIGN_SECRET specifically)
```

### Claim 11 VIOLATES — budget guard was conditional on `redis_client is not None`

```text
Files:
  backend/services/dspy_gepa_runner.py:218 — `if redis_client is not None:` then check budget
  backend/evolution/frontier_crawler.py:143 — same pattern
Codex evidence: when `redis_client` is `None` (documented as
"tests / simulation paths"), the LLM out-call ran without budget guard.
Severity: HIGH (forward-looking — production callback wiring not yet
done; future wiring must pass real client OR the daily ¥20 hard ceiling
would be silently bypassed)
```

## Fixes applied (folded into this commit per session #23/#24/#25 precedent)

### Fix 5 — surplus `## ` heading guard in `_validate_sections`

`backend/services/amendment_drafter.py` — `_validate_sections` now
adds a second pass after the missing-section check: count level-2
(`## `) headings, fail-close if `count != len(MANDATORY_SECTIONS)`.
The check correctly ignores `# ` (level-1 header) and `### ` (level-3
sub-sections under "shadow evidence").

New tests in `tests/test_amendment_drafter.py`:

- `test_validate_sections_rejects_surplus_level2_heading` — appends
  an extra `## extra unauthorized section` and asserts
  `AmendmentSchemaError` is raised with `match="surplus"`.
- `test_validate_sections_accepts_exactly_four_level2_headings` —
  exercises the level-1 + level-3 ignorability with the real
  drafter-emitted shape.

### Fix 8 — rewrite contrast docstring to avoid literal env-var spelling

`backend/services/evolution_feishu_notifier.py` lines 17-22 — rewords
the "we use OpenAPI, NOT custom-bot" contrast text so the literal
`FEISHU_CUSTOM_BOT_*` is no longer spelled in the module. The
amendment reference (P0-2-amendment-2026-05-16 §4 red line 7) is kept,
but the env var names are referenced indirectly. `redline-check.sh`
was already passing because the §5 grep targets the specific
`WEBHOOK_URL|SIGN_SECRET` suffixes — this fix tightens codex's literal
prefix scan too.

### Fix 11 — fail-closed when `redis_client is None`

Two production modules changed to fail-closed on `redis_client=None`:

- `backend/services/dspy_gepa_runner.py` — raises `GEPABudgetError`
  with a clear message ("redis_client is required for GEPA budget
  enforcement (P1-7) ...") AFTER the sample/iter validation checks,
  BEFORE any LLM out-call. Production must always supply a real Redis
  client; tests must supply a stub and monkeypatch
  `assert_budget_allows`.
- `backend/evolution/frontier_crawler.py` — when `summariser` is wired
  and `redis_client` is `None`, the summariser branch is skipped with
  an explicit `errors.append("cost_guard: redis_client is None — summariser skipped (P1-7 budget guard required)")`,
  and `budget_blocked = True`. The codex P2-3 invariant (raw ingest
  must continue during budget breach) is preserved by remaining in
  the document loop and only exiting the summariser sub-branch.
- Updated `run()` docstring on both modules to document the new
  fail-closed contract.

Test updates:

- `tests/test_dspy_gepa_runner.py` — new `stub_budget` fixture (monkeypatches `assert_budget_allows` + returns sentinel) used by 5 happy-path tests (`test_happy_path`, `test_log_dir_partitioned_per_agent`, `test_explicit_max_iterations_propagated`, `test_zero_samples_allowed`, `test_compiler_called_with_seed_examples`).
- New test `test_run_without_redis_client_fails_closed` asserts the new `GEPABudgetError` with `match="redis_client is required"`.
- `tests/test_frontier_crawler.py` — `test_summariser_called_per_document` and `test_summariser_failure_does_not_abort_batch` now pass `redis_client=object()` + monkeypatched `assert_budget_allows` noop.
- New test `test_summariser_without_redis_client_skipped_fail_closed` asserts: raw ingest unaffected (`ingested == 5`), summariser never invoked (`summarised == []`), and the explicit error line ("redis_client is None ... summariser skipped") appears in `result.crawler_errors`.
- `tests/test_evolution_dispatcher.py` — new module-level `stub_budget` fixture (monkeypatches both call sites) consumed by 5 happy-path tests that route through `EvolutionDispatcher.run_prompt_evolution` → `DSPyGEPARunner.run`.
- `tests/test_evolution_e2e.py::TestE2ER1SampleLimitBreach::test_at_exactly_100_allowed` — passes a stub `redis_client=object()` + monkeypatched `assert_budget_allows` to focus on the GEPA sample-count rule (R1) without the new budget gate raising first.

## Cycle 2 — re-verification (all 15 HOLDS)

Tokens used: 66,847. Codex verdict: **PASS**.

| # | Claim | Cycle 1 | Cycle 2 |
|---|-------|---------|---------|
| 1 | GEPA rejects >100 samples typed error | HOLDS | HOLDS |
| 2 | GEPA rejects >MAX_ITER iterations typed error | HOLDS | HOLDS |
| 3 | RAG precision <0.80 fail-closed | HOLDS | HOLDS |
| 4 | Amendment missing mandatory section typed error | HOLDS | HOLDS |
| 5 | Amendment >4 sections typed error | VIOLATES | HOLDS (Fix 5) |
| 6 | Crawler datamarking via `_build_document` | HOLDS | HOLDS |
| 7 | Injection markers counted before HTML strip | HOLDS | HOLDS |
| 8 | No `FEISHU_CUSTOM_BOT_*` reference | VIOLATES | HOLDS (Fix 8) |
| 9 | Category-5 actor enforcement | HOLDS | HOLDS |
| 10 | Scheduler 5 jobs incl. 22:00 evolution shadow run | HOLDS | HOLDS |
| 11 | LLM calls budget-guarded before call | VIOLATES | HOLDS (Fix 11) |
| 12 | No forbidden Phase X imports | HOLDS | HOLDS |
| 13 | Evolution API exactly 3 GET endpoints | HOLDS | HOLDS |
| 14 | PromptRegistry no hot reload | HOLDS | HOLDS |
| 15 | `RagProvenanceEntry` frozen/strict/forbid | HOLDS | HOLDS |

## Local gate before commit

- `pytest tests/` — 3091 passed, 11 skipped (baseline 3087 → +4 net: +1
  surplus-rejects + 1 surplus-accepts + 1 GEPA fail-closed + 1 frontier
  fail-closed; existing tests updated in place stay at the same count).
- `ruff check` — all touched files clean.
- `scripts/redline-check.sh` — All redline checks passed.

## Reaffirmed precedent — [[feedback_codex_findings_real]]

3087 green pytest + ruff + redline + frontend type-check pre-codex was
still NOT commit-safe: 1 cycle of codex with a tight 15-claim
verification prompt found 3 real consistency gaps (1 MEDIUM
defense-in-depth + 1 LOW docstring + 1 HIGH forward-looking budget
bypass). 0 dismissed false positive. The HIGH finding (claim 11) would
have silently allowed the daily ¥20 hard ceiling to be bypassed at
production wiring time — exactly the kind of regression a green test
suite cannot catch.

## Redlines reaffirmed (no regression)

- P0-7 / P0-10 hot-reload all-banned — not touched
- P1-6 credential pool LLM 3 + Feishu 5 unchanged
- P0-2-amendment-2026-05-16 `FEISHU_CUSTOM_BOT_*` permanent ban — now
  enforced literally in `evolution_feishu_notifier.py` (Fix 8)
- P1-7 cost_guard 4 constants (¥20 / 0.70 / ¥440 / ¥4) — Phase X LLM
  out-calls are now strictly budget-guarded fail-closed (Fix 11)
- P2-2 §2 redline 17 — Phase X imports remain zero
- P1-5 §2 redline 1+2 — `backend/api/evolution.py` stays at 3 GETs only
- 0 backend restart / 0 destructive git

## Next round

Proceed to **R2 — Red-Team / Adversarial** (`docs/reviews/x-025-r2-redteam.md`).
