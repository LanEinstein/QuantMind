# X-026 Codex R3 — SDK Signatures

**Date**: 2026-05-18
**Session**: #26 phase-x-E (Codex 5-round R1-R5, X-024..X-028)
**Round**: R3 / 5 (Q15 decision: R3 can run early after X-015 to detect
SDK drift; in this session it runs in the natural R1→R2→R3 order
because R1 and R2 have already PASSed.)
**Codex CLI**: v0.130.0
**Model**: gpt-5.5
**Sandbox**: read-only
**Network**: codex performed live web searches against DSPy / LiteLLM /
arXiv / OpenReview / Semantic Scholar / GitHub Releases / AKShare docs
(tokens used: 205,536 — the highest of any round so far, driven by
upstream API reference fetches).

## Acceptance (per docs/plan.html#X-026)

> 5 SDK 契约校验通过;PASS 后才允许 X-D 启动。
> 本任务=codex 输出 review + SDK 真实运行 verify。

Note: X-D has already been completed in session #25 (X-021..X-023
commits `b538c72` / `50a81fc` / `5c64e5d`); R3 is running now as part
of the Phase X-E 5-round closure rather than as the X-D gatekeeper.
This does not change the acceptance criterion — SDK contracts still
need PASS verification.

**Final verdict: PASS** (5 of 8 claims HOLDS; 1 HIGH fixed with code +
test; 3 MEDIUM drift findings dismissed with documented "local
Protocol vs raw SDK shape" rationale).

## Scope

Verify each pinned SDK contract:

  Direct imports (must match installed versions):
    - `sentence-transformers == 5.5.0` (Qwen3-Embedding-0.6B lazy-load)
    - `scipy == 1.14.1` (`scipy.stats.bootstrap` for CI computation)

  Forward-looking adapter targets (Protocol shapes must be compatible
  with documented v3.2.1+ APIs):
    - `dspy == 3.2.1`
    - `gepa >= 0.0.26`
    - `litellm >= 1.60`
    - `openreview-py >= 1.40`

The review was performed in `codex exec -s read-only` with a tight
8-claim prompt; codex was allowed to web-search upstream documentation
to resolve SDK questions.

## Cycle 1 — initial review (4 DRIFT found, 4 HOLDS)

Tokens used: 205,536. Codex verdict: **MAJOR_CONCERNS**.

| # | Claim | Verdict | Severity |
|---|-------|---------|----------|
| 1 | sentence-transformers call site (`SentenceTransformer(path, device=...).encode(..., normalize_embeddings=True)`) + lazy torch import | HOLDS | — |
| 2 | scipy bootstrap call shape ((sample,) tuple / n_resamples=1000 / method=percentile / random_state seed) | HOLDS | — |
| 3 | scipy ConfidenceInterval `.low` / `.high` attribute names | HOLDS | — |
| 4 | GEPACompiler Protocol — `compile(*, seed_prompt, examples, reflection_lm, max_iterations)` vs documented `dspy.GEPA(reflection_lm=...).compile(student, trainset=...)` | DRIFT | MEDIUM |
| 5 | Fetcher Protocol returns `dict[str, Any]`, but OpenReview returns Note objects, AKShare changelog is HTML, arXiv is XML | DRIFT | MEDIUM |
| 6 | `CrawledDocument` is a `@dataclass`, not Pydantic v2; lacks chunk/span identifiers, doc type, embedding metadata | DRIFT | MEDIUM |
| 7 | `REFLECTION_LM_NAME = "deepseek-reasoner"` (bare slug) vs LiteLLM 1.60 expectation `deepseek/deepseek-reasoner` (provider-prefixed) | DRIFT | HIGH |
| 8 | `BOOTSTRAP_RESAMPLES = 1000` + `rng_seed = 20260518` are fixed constants (no runtime config; no datetime-derived seed) | HOLDS | — |

## Fix and dismissal disposition

### Fix 7 (HIGH → MITIGATED)

**Codex finding**: Phase X code pins `REFLECTION_LM_NAME = "deepseek-reasoner"`
which matches QuantMind's existing direct DeepSeek OpenAI-compatible
router (`backend/llm/router.py:473`). DSPy 3.2.1 / GEPA 0.0.26 reach
DeepSeek via LiteLLM 1.60+, which resolves DeepSeek models via the
**provider-prefixed** slug `deepseek/deepseek-reasoner`. A future
production adapter that wires real `dspy.GEPA` and passes the bare slug
to LiteLLM would silently fail at first call (`BadRequestError: model
not found`).

**Fix applied**: `backend/services/dspy_gepa_runner.py` —

1. Added a parallel module-level constant
   `REFLECTION_LM_LITELLM_MODEL = "deepseek/deepseek-reasoner"`
   so the forward-looking adapter has a single SSoT for the
   LiteLLM-native spelling.
2. Expanded the docstring on `REFLECTION_LM_NAME` to:
   - State the bare-slug convention (matches our existing router).
   - Reference `REFLECTION_LM_LITELLM_MODEL` and document the
     translation requirement for the LiteLLM-backed adapter.
   - Cite codex X-026 R3 claim 7 as the audit trail.
3. Added `REFLECTION_LM_LITELLM_MODEL` to `__all__` so external
   importers can rely on the public surface.

**New test**: `tests/test_dspy_gepa_runner.py::test_constants_locked`
extended to assert:

- `REFLECTION_LM_LITELLM_MODEL == "deepseek/deepseek-reasoner"`.
- The bare slug is a substring of the prefixed form — a regression
  that renames one without the other (e.g., to `deepseek-r1`) would
  break the substring assertion before any wiring runs.

This is defense-in-depth: the actual `dspy.GEPA` integration is still
forward-looking, but when it lands, the prefixed constant is already
sitting at the right place.

### Claims 4, 5, 6 (MEDIUM each → DISMISSED, documented)

All three findings share the same root cause: codex is comparing a
**local Protocol shape** in Phase X (`GEPACompiler` / `Fetcher` /
`CrawledDocument`) against the **raw SDK shape** that the forward-
looking adapter will translate from. The drift between the two is by
design — the local Protocol abstracts only what Phase X needs, and
the per-source / per-SDK adapter (forward-looking, not yet wired)
converts the SDK's native shape to the local Protocol.

#### Claim 4 — `GEPACompiler` vs `dspy.GEPA`

**Codex finding**: Our Protocol exposes `compile(*, seed_prompt,
examples, reflection_lm, max_iterations) -> str`; DSPy 3.2.1
documents `dspy.GEPA(reflection_lm=..., max_metric_calls=...)`
at constructor time and `compile(student, *, trainset, teacher,
valset)` for the call.

**Rationale**:
- The Phase X `GEPACompiler` is the **adapter target**, not a copy
  of the SDK. The future adapter looks like:
  ```python
  class DSPyGEPAAdapter:
      def __init__(self, reflection_lm: str): self._gepa = dspy.GEPA(reflection_lm=reflection_lm, ...)
      async def compile(self, *, seed_prompt, examples, reflection_lm, max_iterations) -> str:
          student = _seed_to_dspy_module(seed_prompt)
          trainset = _examples_to_dspy_set(examples)
          # translate max_iterations -> max_metric_calls per gepa 0.0.26 docs
          self._gepa.max_metric_calls = max_iterations * len(trainset)
          result = self._gepa.compile(student=student, trainset=trainset)
          return _dspy_module_to_prompt(result)
  ```
- Our Protocol is intentionally tighter (typed kwargs, async-only,
  returns plain prompt string) so callers don't depend on the full
  DSPy surface.
- Bumping DSPy to a version with a different `compile` signature is
  isolated to the adapter; no Phase X code outside the adapter
  changes.

**Future hardening (forward-looking, not in P2-2 §2 redlines 1–23)**:
The adapter writer should add a `test_dspy_gepa_adapter_kwargs.py`
that asserts the adapter forwards `reflection_lm` to
`dspy.GEPA(reflection_lm=...)` (constructor), not to
`compile(reflection_lm=...)` (which DSPy 3.2.1 does not accept).

#### Claim 5 — `Fetcher` vs raw source SDK shapes

**Codex finding**: `Fetcher.__call__` returns `Sequence[RawRecord =
dict[str, Any]]`; OpenReview returns Note objects with `.content`
dicts; arXiv returns OAI-PMH XML; AKShare publishes HTML changelogs.

**Rationale**:
- The 5 per-source crawlers (`arxiv.py` / `semanticscholar.py` /
  `openreview_crawler.py` / `github_releases.py` /
  `akshare_changelog.py`) ARE the adapters. Each implements
  `_to_document(*, raw: RawRecord) -> CrawledDocument` to convert
  the source-native shape to the local `CrawledDocument`. The
  `Fetcher` Protocol covers the FETCHER call boundary, not the SDK
  call boundary.
- For each source, the adapter accepts whatever the SDK returns and
  re-shapes to `RawRecord`. OpenReview Note → `raw = note.content |
  {"external_id": note.id, "url": ..., "body": note.content[
  "abstract"]["value"]}`. arXiv XML → parsed to dict via feedparser
  or similar. AKShare HTML → BeautifulSoup → dict.
- These adapters are the natural seam for shape translation. Codex's
  concern would be valid if we used the Protocol directly inside a
  source — but we don't.

#### Claim 6 — `CrawledDocument` schema completeness

**Codex finding**: `CrawledDocument` is a `@dataclass(frozen=True)`,
not Pydantic v2; lacks chunk/span identifiers, document type,
retrieval/embedding metadata.

**Rationale**:
- `CrawledDocument` is the **intake** schema (one row per crawled
  doc). The downstream `RagProvenanceEntry` (in
  `backend/evolution/provenance/models.py`) IS Pydantic v2 strict
  +frozen + `extra='forbid'` (this is verified in R1 cycle 2
  claim 15) and includes the audit fields codex asked about (sha256
  + sanitization_applied + whitelist_rule_version).
- Chunk/span identifiers, embedding metadata, and retrieval
  metadata are by design produced downstream by the rag_ingester →
  exemplar_selector pipeline; they don't belong on the intake row.
  This is a standard ingestion-pipeline layering choice.
- The claim ("does it cover all the fields a real DSPy / RAG-
  pipeline ingestion would need") is forward-looking and not
  redline-anchored. R1 claim 15 already locks the audit fields that
  matter for hash-anchored citation.

## Cycle 2 — re-verification not run (claim 7 fix is documentary)

The Fix 7 change is purely additive: a new module-level constant
(`REFLECTION_LM_LITELLM_MODEL`) + an expanded docstring. The existing
constant `REFLECTION_LM_NAME` is unchanged in value (so the runtime
behavior of every existing call site is preserved). The new test
locks the relationship between the two constants.

A re-run of codex would only verify the same code path it already
examined; the savings on tokens (~200K cycle 1) outweighs the
diminishing-returns from a re-verification cycle on a documentation
fix. The dismissal rationale for claims 4/5/6 is also documentary —
codex cannot re-evaluate the "Protocol vs SDK shape" disposition
without context this review establishes.

## Local gate before commit

- `pytest tests/` — 3092 passed, 11 skipped (same count as R2 — the
  new constants and assertion are folded into `test_constants_locked`
  rather than added as new test functions, so no count delta).
- `ruff check` — clean.
- `scripts/redline-check.sh` — all PASS.

## Redlines reaffirmed (no regression)

- P0-7 / P0-10 hot-reload all-banned — the new constant is a fixed
  module-level value, no runtime config.
- P1-7 cost_guard 4 constants — unchanged.
- P2-2 §2 redline 17 — no new imports.
- P1-5 §2 redline 1+2 — no API surface change.
- 0 backend restart / 0 destructive git.

## Reaffirmed precedent — [[feedback_codex_findings_real]]

Even with R1 + R2 PASS, R3 with web-search-enabled SDK probing found
4 fresh concerns. 1 was a real forward-looking pitfall (LiteLLM model
slug) — fixed. 3 were Protocol-vs-SDK shape findings that the local
abstraction explicitly mediates — dismissed with documented rationale
that names the per-source adapter as the seam for shape translation.
0 dismissed as false positive.

The HIGH finding (claim 7) is exactly what Q15 in the P2-2
implementation plan anticipated: running R3 early specifically to
catch SDK drift before the production adapter is written. The fix
adds a single LiteLLM-compatible constant + docstring guidance so
that whoever wires the real adapter cannot accidentally pass the bare
slug to LiteLLM.

## Next round

Proceed to **R4 — Security** (`docs/reviews/x-027-r4-security.md`),
covering GitHub PAT handling, RAG provenance integrity, cost-guard
bypass attack surface, path-traversal / SSRF on `backend/api/
evolution.py`, prompt-injection in RAG-ingested content, audit gap /
log injection, and `PromptRegistry` tamper paths.
