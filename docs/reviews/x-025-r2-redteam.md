# X-025 Codex R2 — Red-Team / Adversarial

**Date**: 2026-05-18
**Session**: #26 phase-x-E (Codex 5-round R1-R5, X-024..X-028)
**Round**: R2 / 5
**Codex CLI**: v0.130.0
**Model**: gpt-5.5
**Sandbox**: read-only

## Acceptance (per docs/plan.html#X-025)

> 0 critical 攻击路径;PASS 才进下一轮。

**Final verdict: PASS** (0 CRITICAL; 1 HIGH fixed; 3 HIGH/MEDIUM dismissed with documented trust-boundary rationale).

## Scope

Probe Phase X for attack paths across 10 enumerated adversarial scenarios:
shadow-validate bypass, human-gate bypass, prompt-injection via RAG ingest,
provenance content swap, cost-guard bypass, Kimi sub-cap evasion, audit
tampering, and Feishu chat-id steering.

The review was performed in `codex exec -s read-only` mode with a tight
10-scenario prompt and a hard cap of ≤ 25 grep/read commands.

## Cycle 1 — initial review (4 EXPLOITABLE found, 6 BLOCKED)

Tokens used: 55,786. Codex verdict: **MAJOR_CONCERNS**.

| # | Scenario | Cycle 1 | Severity |
|---|----------|---------|----------|
| 1 | Stub ShadowChain accepted by dispatcher | EXPLOITABLE | HIGH |
| 2 | Missing strict-better metric defaults pass | BLOCKED | — |
| 3 | Bootstrap CI randomness drift | BLOCKED | — |
| 4 | NBSP mandatory section marker bypass | BLOCKED | — |
| 5 | Spotlight END sentinel closes wrapper | BLOCKED | — |
| 6 | Forged doc_id path traversal | BLOCKED | — |
| 7 | Latest-wins provenance swap | EXPLOITABLE | HIGH |
| 8 | Cost guard retry / partial-spend bypass | EXPLOITABLE | HIGH |
| 9 | Kimi cap failover evasion | BLOCKED | — |
| 10 | Feishu arbitrary chat via constructor | EXPLOITABLE | MEDIUM |

## Fix and dismissal disposition

### Fix 8 (HIGH → MITIGATED via defense-in-depth)

**Codex finding**: GEPA performs one point-in-time budget check before
`self.compiler.compile()`. A future DSPy/gepa SDK that internally retries
or makes more LLM calls than the pre-check estimated would over-spend
without a typed error firing.

**Fix applied**: `backend/services/dspy_gepa_runner.py` — added a
POST-compile `assert_budget_allows` call wrapped in
`try/except DailyBudgetExceededError → raise GEPABudgetError` with the
agent name suffixed `:post_compile`. The pre-check still gates ENTRY;
the post-check gates the NEXT run from compounding the overrun. This is
defense-in-depth — the SDK-level cap (`GEPA_MAX_BUDGET_CNY=5.0`) is the
primary protection; the post-check catches SDK regressions that bypass
the internal cap.

**New test**: `tests/test_dspy_gepa_runner.py::test_post_compile_budget_breach_raises`
exercises the pre-check OK + post-check breach scenario and asserts
the typed `GEPABudgetError` is raised with `match="POST-compile"`.

### Scenario 1 (HIGH → DISMISSED, documented)

**Codex finding**: `EvolutionDispatcher.run_prompt_evolution` trusts
`verdict.passed`; does not "independently re-check"
`champion_passed_all_gates` / `challenger_passed_all_gates`.

**Rationale for dismissal**: `ChallengerVerdict.passed` is a
`@property` that is **the AND of those two fields plus
`challenger_strictly_better_on_all_four` and
`challenger_within_tolerance_on_all_four`** (see
`backend/services/shadow_chain.py:248`). Re-reading the individual
fields would just duplicate the property logic without adding any
adversarial barrier. The "independent re-check" would have to be a
fresh metric replay (which is what `ShadowChain.run` already does);
duplicating it from the dispatcher would defeat the abstraction.

**Trust boundary**: the `ShadowChain` instance is injected at
`EvolutionDispatcher` construction time in production wiring (main.py
/ broker scheduler 5th-cron callback). An attacker who can swap the
injected `ShadowChain` already has code execution and can break far
worse invariants. The threat is "DI compromise", not Phase X.

### Scenario 7 (HIGH → DISMISSED, documented)

**Codex finding**: `ProvenanceVerifier.lookup` selects the latest
ledger row for a `doc_id`; `content_sha256` is verified after
selection, not used to select the intended citation. An attacker who
appends a new provenance row + writes a matching file under
`data/rag/{source}/{date}/{doc_id}.md` could shift the latest pointer.

**Rationale for dismissal**:

1. **Latest-wins is intentional** — per `lookup` docstring: "the X-004
   rejection path appends a new entry when re-ingesting after a
   sanitisation failure — the verifier should see the latest decision,
   not a superseded one". Disabling re-ingest under same `doc_id` would
   break the sanitisation-retry path.
2. **The hash anchor defends the primary attack** — in-place file
   tampering (overwriting the .md content without touching the ledger)
   is what the sha256 check catches.
3. **Trust boundary**: for the codex-described attack to succeed, the
   attacker needs WRITE access to BOTH `data/rag/provenance.jsonl` AND
   the file under `data/rag/{source}/{date}/`. Per CLAUDE.md §2.9 +
   P1-6 §1.5, these directories are 127.0.0.1-only behind SSH tunnel,
   not exposed to any network. A full filesystem compromise can break
   far worse invariants — not specific to Phase X.
4. **Future hardening option**: if a future amendment elevates this to
   a hard requirement, the writer could reject duplicate `doc_id`
   entries OR the verifier could require an explicit `as_of` cursor.
   Both are forward-looking and not in P2-2 §2 redlines 1–23.

### Scenario 10 (MEDIUM → DISMISSED, documented)

**Codex finding**: `FeishuAlerter` constructor takes `alert_chat_id:
str` as a kwarg; nothing forces production to pass
`os.environ["FEISHU_ALERT_CHAT_ID"]`.

**Rationale for dismissal**:

1. **Constructor injection is the documented test interface** — the
   alerter is unit-tested with `oc_alert` placeholder chat ids and
   stub `FakeFeishu` clients. Hard-coding the env var inside the
   constructor would break every test that wires the alerter.
2. **Trust boundary**: production wiring (`backend/main.py` / scheduler
   bootstrap) is the place that maps `FEISHU_ALERT_CHAT_ID` env →
   `alert_chat_id` kwarg. That wiring is reviewed manually at
   deployment time. An attacker who can modify the wiring code already
   has code execution.
3. **Per-call steering is locked** — codex's same finding confirms:
   "per-call steering is blocked; the `_alert_chat_id` is read from
   `self` not from the call payload" (`backend/integrations/feishu/
   alerter.py:258`). So input data (e.g., a hostile audit payload)
   cannot redirect the alert.

## Cycle 2 — re-verification (Fix 8 BLOCKED; 3 dismissed as documented)

After applying Fix 8 and re-running the local stack
(`pytest tests/` = 3092 passed, 11 skipped; `ruff check` clean;
`redline-check.sh` PASS), the post-fix state is:

| # | Scenario | Disposition |
|---|----------|-------------|
| 1 | Stub ShadowChain accepted by dispatcher | DISMISSED — DI trust boundary above the dispatcher |
| 2 | Missing strict-better metric defaults pass | BLOCKED (cycle 1) |
| 3 | Bootstrap CI randomness drift | BLOCKED (cycle 1) |
| 4 | NBSP mandatory section marker bypass | BLOCKED (cycle 1) |
| 5 | Spotlight END sentinel closes wrapper | BLOCKED (cycle 1) |
| 6 | Forged doc_id path traversal | BLOCKED (cycle 1) |
| 7 | Latest-wins provenance swap | DISMISSED — filesystem trust boundary; doc_id re-ingest is intentional |
| 8 | Cost guard retry / partial-spend bypass | MITIGATED — Fix 8 post-compile assertion + new regression test |
| 9 | Kimi cap failover evasion | BLOCKED (cycle 1) |
| 10 | Feishu arbitrary chat via constructor | DISMISSED — production-wiring trust boundary; per-call steering already locked |

## Local gate before commit

- `pytest tests/` — 3092 passed, 11 skipped (3091 → +1: the new
  `test_post_compile_budget_breach_raises`).
- `ruff check` — clean.
- `scripts/redline-check.sh` — all PASS.

## Redlines reaffirmed (no regression)

- P0-7 / P0-10 hot-reload all-banned — untouched.
- P0-2-amendment-2026-05-16 `FEISHU_CUSTOM_BOT_*` ban — untouched (Fix
  8 of session #26 R1 already locked the docstring).
- P1-7 cost_guard 4 constants (¥20 / 0.70 / ¥440 / ¥4) — the post-compile
  assertion uses the SAME `assert_budget_allows` helper with the same
  Redis-backed daily counter; no new constant.
- P2-2 §2 redline 17 — Phase X imports remain zero.
- P1-5 §2 redline 1+2 — no API surface change.
- 0 backend restart / 0 destructive git.

## Reaffirmed precedent — [[feedback_codex_findings_real]]

Even with R1 PASS in cycle 2 (claim 11 mandatory budget guard now
fail-closed), the red-team round found 4 NEW concerns. 1 was a real
defense-in-depth gap (post-compile budget) — fixed. 3 were trust-
boundary findings (DI / filesystem / production wiring) that the
threat model places above the Phase X module surface — dismissed with
documented rationale. 0 dismissed false positive.

## Next round

Proceed to **R3 — SDK Signatures** (`docs/reviews/x-026-r3-sdk.md`),
covering DSPy 3.2.1 / gepa 0.0.26 / LiteLLM 1.60 / openreview-py 1.40
/ Qwen3-Embedding-0.6B / scipy.stats.bootstrap.
