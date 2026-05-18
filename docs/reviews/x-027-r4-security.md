# X-027 Codex R4 — Security

**Date**: 2026-05-18
**Session**: #26 phase-x-E (Codex 5-round R1-R5, X-024..X-028)
**Round**: R4 / 5
**Codex CLI**: v0.130.0
**Model**: gpt-5.5
**Sandbox**: read-only
**Tokens used**: 78,434

## Acceptance (per docs/plan.html#X-027)

> 0 critical 安全漏洞;PASS 才进 R5。

**Final verdict: PASS** (0 CRITICAL; 1 HIGH fixed; 1 LOW fixed; 2
MEDIUM forward-looking dismissed; 3 UNCLEAR documented as
out-of-scope-or-deployment-boundary).

## Scope

Probe 10 security scenarios across secret handling (GitHub PAT), RAG
provenance integrity, path-traversal / SSRF on the evolution API,
cost-guard bypass, prompt-injection in RAG ingest, audit gap, and
PromptRegistry tampering.

## Cycle 1 — initial review (5 VULNERABLE, 2 SECURE, 3 UNCLEAR)

| # | Scenario | Cycle 1 | Severity |
|---|----------|---------|----------|
| 1 | GitHub PAT crawler wiring | VULNERABLE | MEDIUM |
| 2 | GitHub PAT startup validation | VULNERABLE | MEDIUM |
| 3 | GitHub PAT plaintext logging | SECURE | — |
| 4 | Provenance lock DoS | VULNERABLE | LOW |
| 5 | RAG fs permissions | UNCLEAR | — |
| 6 | Pending API symlink path traversal | VULNERABLE | LOW |
| 7 | Precision window bounds | SECURE | — |
| 8 | IPv4-only crawler egress | UNCLEAR | — |
| 9 | Cost spend recording order | UNCLEAR | — |
| 10 | PromptRegistry path traversal | VULNERABLE | HIGH |

## Fixes applied

### Fix 10 (HIGH → MITIGATED) — PromptRegistry path-traversal containment

**Codex finding**: `PromptVersionEntry._check_path_and_hash` validated
only that the path **starts with** `config/prompts/` and ends with
`.yaml`. A path like `"config/prompts/../etc/passwd.yaml"` passes both
checks but resolves to `etc/passwd.yaml` once `root / entry.path` is
evaluated at load time. The downstream `prompt_path.read_bytes()` +
`compute_prompt_sha256()` would then read and hash an arbitrary file.

**Fix applied**: `backend/services/prompt_registry.py` — three new
fail-closed checks layered on the existing prefix/suffix gates:

1. **`..` component rejection** — reject any path where
   `PurePosixPath(self.path).parts` contains the literal `..`
   element. This catches `config/prompts/../etc/passwd.yaml` as well
   as deeper escapes like `config/prompts/foo/../../etc/passwd.yaml`.
2. **Absolute path rejection** — reject paths starting with `/`.
3. **Backslash separator rejection** — reject paths containing `\` to
   close the Windows-style escape `config/prompts\..\etc\passwd.yaml`
   that some path libraries normalise differently from `/`.

The check raises `ValueError` at Pydantic model construction time, so
the malformed lockfile fails at `PromptLockFile.model_validate_json`
during `PromptRegistry.from_lockfile()` — well before any
`read_bytes()` call.

**New tests**: 3 in `tests/test_prompt_registry_schema.py` lock the
new rejection paths:

- `test_version_entry_rejects_path_traversal_in_components` — asserts
  `match="path-traversal"` on `config/prompts/../etc/passwd.yaml`.
- `test_version_entry_rejects_absolute_path` — `/etc/passwd.yaml`
  rejected.
- `test_version_entry_rejects_backslash_path` — Windows-style
  separators rejected.

### Fix 6 (LOW → MITIGATED) — Pending API symlink rejection

**Codex finding**: `GET /api/evolution/pending` iterates
`docs/decisions/pending/` and accepts any `.md` file where
`path.is_file()` is True. Because `Path.is_file()` follows symlinks,
a `pending/draft.md → /etc/passwd` symlink would be served (size +
mtime exposed; full content readable to the caller if the API ever
exposes the body — currently only metadata is exposed but the
defensive check should still hold for forward-looking endpoints).

**Fix applied**: `backend/api/evolution.py` —
`for path in pending_dir.iterdir():` now checks `path.is_symlink()`
**first**; symlinks are logged and skipped via
`log.warning("evolution_pending_symlink_skipped path=%s", path)`. The
existing `is_file()`/`.md` checks run afterwards on real files only.

**New test**: `tests/test_api_evolution.py::test_pending_rejects_symlinks`
creates a `pending/draft.md -> outside.md` symlink and asserts the
listing returns `count=0` (the symlink is suppressed).

## Dismissals (with documented rationale)

### Claim 1 + 2 (MEDIUM each → DISMISSED, forward-looking)

**Codex findings**:

- (1) `GitHubReleasesCrawler` / `CrawlerBase` do not wire the GitHub
  PAT auth header.
- (2) `backend/services/secrets_validator.py` does not validate
  `GITHUB_TOKEN` / `GITHUB_PAT` at boot.

**Rationale**:

1. **Phase X crawlers are Fetcher-Protocol-abstracted** — the
   per-source `_to_document(*, raw: RawRecord)` shim only sees the
   converted dict. The actual HTTP call lives in an injected
   `FetcherCallable`. Production wiring (not yet landed — same
   forward-looking status flagged in R3 claim 4) provides the
   concrete fetcher, which is the natural place to load PAT from
   `os.environ["GITHUB_TOKEN"]` and to build the auth header.
2. **The PAT is explicitly excluded from the secrets_validator
   pool** by Q3 of P2-2-implementation-plan-2026-05-18 (PAT is
   "heterogeneous to the LLM 3 + Feishu 5 pool; NOT added to the
   pool"). The validator's docstring at line 64–65 makes this
   explicit: 3 LLM + 5 Feishu = 8 names, with custom-bot legacy
   names explicitly absent.
3. **Future hardening at the production wiring session**: the
   github_releases crawler implementation MUST load the PAT from
   `os.environ` only, use it as a Bearer auth header, and avoid any
   `log.info` / `log.error` site that prints the raw token. A
   separate optional GITHUB_TOKEN check at the crawler init (not
   the global validator) will surface a typed
   `MissingCrawlerCredentialError` if the env var is unset and the
   GitHub source is enabled.

### Claim 4 (LOW → DISMISSED, deployment-boundary)

**Codex finding**: `ProvenanceWriter.append_line` uses
`fcntl.flock(fd, fcntl.LOCK_EX)` (blocking, no timeout). Any local
process that acquires the lock and never releases it can DoS the
writer.

**Rationale**:

1. **Threat model**: a local process compromise is required to hold
   the lock indefinitely. Per CLAUDE.md §2.9 + P1-6 §1.5, the host
   is 127.0.0.1-only behind SSH tunnel; an attacker who already has
   local code execution can break far worse invariants than the
   provenance writer.
2. **Loss-of-availability ≠ loss-of-integrity**: the lock is a
   write-serialisation primitive, not an integrity primitive. The
   blocking semantics are intentional — the writer SHOULD wait for
   the lock rather than dropping rows.
3. **Future hardening (optional)**: introduce a bounded
   `LOCK_EX | LOCK_NB` + exponential-backoff retry (max 5 attempts,
   ~3 seconds total) to bound the wait. Not landed in this round
   because the existing tests assert blocking semantics and the
   threat is local-process-only.

### Claim 5 (UNCLEAR → DOCUMENTED)

**Codex finding**: no explicit `os.chmod(0o700/0o600)` on
`data/rag/` subtree.

**Rationale**: the deployment boundary is the host filesystem
(127.0.0.1-only behind SSH tunnel, P1-6 §1.5). The redline does not
require filesystem-level chmod beyond Linux defaults; the operator
controls user/group on the host. Adding application-layer chmod
would be defense-in-depth but is not in P2-2 §2 redlines 1–23 and
would risk breaking owner workflow if the runtime user differs from
the deploy user.

### Claim 8 (UNCLEAR → DOCUMENTED, forward-looking)

**Codex finding**: no outbound HTTP client implementation in the
listed Phase X files, so IPv4-only binding cannot be verified.

**Rationale**: the crawlers use `FetcherCallable` (the Protocol
abstraction); the real HTTP client is a production-wiring concern
(same forward-looking status as R3 claim 5). When the production
adapter lands, it MUST use a shared `httpx.AsyncClient` with
`local_address="0.0.0.0"` per [[feedback_ipv4_only_egress]]:

> AAAA-publishing hosts (dashscope) silently stall every parallel
> agent call when local_address is unset on this host.

A separate `test_crawler_ipv4_egress.py` should lock the binding at
the production adapter site.

### Claim 9 (UNCLEAR → OUT-OF-SCOPE)

**Codex finding**: spend-recording order is "outside the allowed
files" — codex couldn't trace it within the R4 scope.

**Rationale**: `cost_guard.assert_budget_allows` is the PRE-flight
check; actual spend RECORDING happens in `backend/llm/router.py` /
`backend/services/cost_probe.py` after each LLM call returns and is
outside Phase X by import-isolation (P2-2 §2 redline 17). The R4
prompt deliberately scoped to Phase X files; verifying the recording
order is a CLAUDE.md §2.10 + P1-7 invariant outside this round's
remit.

The R2 scenario 8 fix (post-compile budget re-assertion in
`dspy_gepa_runner`) already addresses the practical concern: even if
spend recording is delayed, the next `assert_budget_allows` call
catches the breach.

## Cycle 2 — re-verification not run (parallel to R3 disposition)

The HIGH fix (PromptRegistry containment) and LOW fix (symlink
rejection) are both purely additive validation gates with new
regression tests. The cost of re-running codex (~80K tokens with
filesystem and web search) to confirm two added rejections is not
proportional to the value gained — the tests themselves are the
verification.

## Local gate before commit

- `pytest tests/` — 3096 passed, 11 skipped (3092 → +4: 3 prompt
  registry path-traversal tests + 1 pending symlink test).
- `ruff check` — clean.
- `scripts/redline-check.sh` — all PASS.

## Redlines reaffirmed (no regression)

- P0-7 / P0-10 hot-reload all-banned — PromptRegistry validator is
  still strict + fail-closed at module construction; no runtime
  config knob added.
- P1-7 cost_guard 4 constants — untouched.
- P2-2 §2 redline 17 — Phase X imports remain zero.
- P1-5 §2 redline 1+2 — `backend/api/evolution.py` stays at 3 GETs.
  The symlink rejection is internal to the existing pending handler,
  not a new route.
- P1-6 §1.1 credential pool LLM 3 + Feishu 5 — UNCHANGED; the PAT
  remains explicitly outside the validator pool per Q3.
- P0-2-amendment-2026-05-16 — `FEISHU_CUSTOM_BOT_*` still permanently
  banned (R1 cycle 2 claim 8 verification).
- 0 backend restart / 0 destructive git.

## Reaffirmed precedent — [[feedback_codex_findings_real]]

Even with R1+R2+R3 PASS, R4 found 5 real concerns: 1 HIGH (path
traversal that would have allowed reading arbitrary files via a
malformed lockfile) and 1 LOW (symlink path traversal in the pending
API) were fixed; 3 forward-looking / deployment-boundary findings
were dismissed with documented rationale. 0 dismissed false positive.

The HIGH finding (PromptRegistry path traversal) is exactly the kind
of regression a green test suite cannot catch — the existing test
`test_version_entry_rejects_path_outside_config_prompts` tested an
"outside the prefix" path, but not a path that LOOKS like it's inside
the prefix yet escapes via `..`. The fix + 3 new regression tests
plug the gap.

## Next round

Proceed to **R5 — Coverage** (`docs/reviews/x-028-r5-coverage.md`),
the closure round. Verify Phase X module coverage ≥ 80% + R1 / R3 /
R7 hard-constraint negative assertions are covered.
