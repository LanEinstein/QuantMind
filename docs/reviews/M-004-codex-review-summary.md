# M-004 Codex Review Summary — InstructionPlan 单一构造点红线 + 对抗测试

> Task: M-004 `InstructionPlan 单一构造点红线 + 对抗测试 + redline-check 子检`
> (R0 §4 red line B). Scope reviewed: `scripts/redline-check.sh` ([M-004] block)
> + `tests/test_instructionplan_provenance.py` (new). No production code change —
> the builder was already provenance-clean (construction only in model + builder;
> `proposal_text` is consumed only to pick `side`, never reaching numeric fields).
> Gate: 1 review cycle + read-only verify (2 fix rounds).

## Local gate (pre-codex)

- `pytest tests/test_instructionplan_provenance.py` → 10 passed.
- Full suite → 3459 passed / 11 skipped.
- `ruff` clean; `scripts/redline-check.sh` → all green incl. new `[M-004]`.

## What M-004 locks

- **redline-check `[M-004]`**: an alias-aware AST scanner asserts
  `InstructionPlan(...)` is constructed only in `{backend/models/instruction.py,
  backend/services/instruction_plan_builder.py}` anywhere under `backend/`.
- **Adversarial provenance tests**: a numeric / direction-laden
  `FundManagerOutput.proposal_text` ("BUY 5000 ... SELL SELL SELL ... 12345")
  never sets `volume`/`limit_price` (they come from `AssemblyContext`), never
  overrides `side` (typed enum), and never appears on the constructed plan; a
  `parse_ok=False` BUY still yields a HOLD plan.
- **Static construction-site scan** (pytest, mirrors the redline scanner).

## Cycle 1 — `codex review --uncommitted`

**1 finding, P2 (no P0/P1).**

- **[P2] Resolve InstructionPlan calls instead of grepping text** —
  `scripts/redline-check.sh`. The original raw-text grep had false negatives:
  an aliased import (`from ... import InstructionPlan as Plan; Plan(...)`) or an
  attribute call (`m.InstructionPlan(...)`) evade it. Since this is the CI gate
  for the provenance red line, it must resolve AST call targets/aliases.

  **Fix:** replaced the grep with a Python AST scanner (in both the redline
  check and the pytest `_construction_sites`) that seeds the local names bound
  to `InstructionPlan` via `ImportFrom` and flags `ast.Call` to those names or
  to any `.InstructionPlan` attribute. Added 3 regression tests
  (alias / attribute construction flagged; mention-only not flagged).

## Cycle 2 — `codex exec --sandbox read-only` (verify, round 1)

**Verdict: FAIL** — found a *new* gap: `backend/models/__init__.py` re-exports
`InstructionPlan`, so `from backend.models import InstructionPlan as Plan;
Plan(...)` was still missed because the scanner only seeded names from modules
ending in `models.instruction`.

  **Fix:** broadened both scanners to seed from **any** `ImportFrom` importing
  the name `InstructionPlan` (the repo has exactly one such class, so matching
  the imported name is safe; a type-hint-only import with no construction Call
  is never flagged). Added `test_scanner_flags_reexport_alias_construction`.
  Re-ran: 10 passed, redline + ruff green.

## Cycle 2 — verify round 2

**Verdict: PASS** — scanner now catches direct import, re-export alias, aliased
import, and attribute (`m.InstructionPlan(...)`) construction; type-hint-only
imports are not flagged; scanning the current `backend/` tree finds only
`instruction_plan_builder.py` as a real construction site (model file holds the
class def, not a call). 0 false positives. (codex's in-sandbox pytest run
couldn't start due to a sandbox temp-dir limitation; the 10 tests pass locally.)
