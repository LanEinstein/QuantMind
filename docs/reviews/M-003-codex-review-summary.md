# M-003 Codex Review Summary — 4 必经 agent 单轮辩论(MVP)

> Task: M-003 `4 必经 agent 单轮辩论(MVP)` — replace the M-002 deterministic
> stubs with the four mandatory LLM agents + a single-round debate, fund_manager
> as sole proposer, fail-closed HOLD degradation, and the LLM-only
> `FundManagerOutput` bridge.
> Scope reviewed (staged uncommitted): `backend/agents_team/{agents.py(new),
> state.py, nodes.py, graph.py, __init__.py}` + `tests/agents_team/*`.
> Gate: 1 cycle review + 1 read-only verify (per CLAUDE.md §3 codex-review
> mandatory pre-commit for code tasks).

## Local gate (pre-codex)

- `pytest tests/agents_team/` → 57 passed; `--cov=backend.agents_team` **100%**.
- Full suite `pytest tests/` → 3448 passed / 11 skipped (baseline 3425 → +23).
- `ruff check backend/agents_team tests/agents_team` → clean.
- `scripts/redline-check.sh` → all green (incl. [L-002] pure-quant isolation,
  no new violations).
- No live production caller of `run_team` / `build_team_graph` yet (the cost
  guard reserve wrapping is M-005's task before go-live; no un-budgeted live
  LLM path introduced).

## Cycle 1 — `codex review --uncommitted`

**1 finding, P2 (no P0/P1/P3).**

- **[P2] Treat whitespace-only agent responses as missing** —
  `backend/agents_team/agents.py:105-107`.
  A provider returning a whitespace-only completion (e.g. `"   "`) for an
  analyst made `_complete` return that string unchanged; the mandatory-agent
  gate (`_missing_mandatory` in `nodes.py`) reads it as truthy, so the graph
  could satisfy the mandatory-report check and reach `BUILD_OK` with no usable
  report — defeating the intended fail-closed behavior.

  **Fix:** `_complete` now returns `""` when
  `not isinstance(content, str) or not content.strip()`, so whitespace-only
  completions fail closed exactly like empty / None / no-choices responses.
  Regressions added:
  - `test_analyst_fail_closed_on_whitespace_only` (node level);
  - `test_graph_whitespace_only_agent_cannot_reach_build_ok` (end-to-end →
    HOLD with the missing agent named in the reason).

  Re-ran: 57 passed, 100% module coverage, ruff clean.

## Cycle 2 — `codex exec --sandbox read-only` (verify)

**Verdict: PASS** (no file changes by codex; verified with an in-memory smoke
check). `_complete()` returns `""` for whitespace-only / empty / `None` /
malformed / no-choices completions while preserving normal nonblank content;
a whitespace-only analyst report → `fundamental_report=""` → `_missing_mandatory`
flags it → `builder_node` returns HOLD `mandatory_agent_missing:...`, never
BUILD_OK. Valid fund_manager JSON still parses to BUY/SELL/HOLD with reasoning
and `fund_manager_parse_ok=True`. 0 regressions.

## Invariants held (safety floor unchanged)

- LLM writes ONLY free text + the `direction` proposal; the deterministic tool
  nodes (`risk_gate` / `builder`) read numeric state only — no LLM edge writes
  the decision path (R0 §4). Determinism tests (different report text → identical
  risk/decision output) still pass.
- `fund_manager` is the sole BUY/SELL/HOLD proposer; any mandatory agent missing
  / empty / whitespace-only → degrade HOLD; `debate_round_count ≥ 1` enforced.
- agents_team NEVER constructs an `InstructionPlan`; the only handoff is the
  LLM-only `FundManagerOutput` bridge (`to_fund_manager_output`) — numeric order
  fields derived by `instruction_plan_builder` (single construction point, M-004).
- Local SQLite checkpointer only (no hosted SaaS); `TeamContext` (router + engine)
  never enters the checkpointed `TeamState`.
- MVP cost shape: 4 LLM calls per candidate (3 analysts + fund_manager; debate is
  a deterministic fan-in) — `test_graph_makes_exactly_four_llm_calls`.
