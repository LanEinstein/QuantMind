# P5B Exit Harness — Codex Review Summary

**Scope**: backend/services/{shadow_recorder,shadow_compare,phase5b_exit_check}.py + scripts/{shadow_compare,phase5b_exit_check}.py + tests/{test_shadow_recorder,test_shadow_compare,test_phase5b_exit_check,test_scripts_*}.py + backend/data/database.py shadow_decisions index spec.

**Cycles**: R1 (architecture / open) → R2 (followup) → R3 (perf) → R4 (testing) → R5 (security) → R6 (final verify) → R7 (final verify after R6 unresolved).

**Final Verdict**: ✅ PASS (R7 closure check).

## Issues found per round

| Round | Focus | P1 | P2 | P3 | Verdict |
|------:|-------|---:|---:|---:|---------|
| R1 | architecture / correctness | 2 | 1 | 1 | NEEDS_FIXES |
| R2 | followup verification | 0 | 1 | 1 (INFO) | NEEDS_FIXES |
| R3 | performance | 0 | 1 | 1 | NEEDS_FIXES |
| R4 | testing | 0 | 2 | 3 (LOW) | NEEDS_FIXES |
| R5 | security | 0 | 2 | 2 (LOW) | NEEDS_FIXES |
| R6 | final verify (1st) | — | — | — | PARTIAL (issue #6 incomplete) |
| R7 | final verify (2nd) | — | — | — | PASS |

## Issue ledger (15 total, all RESOLVED)

| # | Round | Severity | Issue | File |
|---|------|----------|-------|------|
| 1 | R1 | P1 | ISO timestamp parsing | phase5b_exit_check.py |
| 2 | R1 | P1 | Missing cost telemetry → fail-open | phase5b_exit_check.py |
| 3 | R1 | P2 | CLI window filter missing | scripts/phase5b_exit_check.py |
| 4 | R1 | P3 | has_data lookup by partial key | phase5b_exit_check.py |
| 5 | R2 | WARN | KeyError on missing run_id | phase5b_exit_check.py |
| 6 | R2 | P2 | Partial cost telemetry pass | phase5b_exit_check.py |
| 7 | R2 | INFO | Markdown pipe injection | phase5b_exit_check.py |
| 8 | R3 | P2 | CLI no projection | scripts/phase5b_exit_check.py |
| 9 | R3 | P3 | shadow_decisions no indexes | database.py |
| 10 | R4 | MED | CLI live path untested | tests |
| 11 | R4 | MED | _coerce_leg accepted invalid actions | shadow_compare.py |
| 12 | R4 | LOW | Index regression not locked | tests |
| 13 | R5 | MED | shadow_decisions no TTL | database.py |
| 14 | R5 | MED | --days unbounded + JSONL DoS | scripts/* |
| 15 | R5 | LOW | trade_date markdown injection | shadow_compare.py |

## Test baseline

- 1077 passed, 11 skipped, 0 failed (vs. P5B-T03 baseline 968 passed → +109 net).
- Coverage: backend overall 83% (vs T03 82.47%); backend/risk 98%; backend/services/shadow_recorder 100%; shadow_compare 94%; phase5b_exit_check 97%.
- `grep -rn 'from backend\\.llm\\|from backend\\.agents\\|from backend\\.mirofish' backend/risk/` clean (only docstring hit).

## Round files
- [R1 architecture/open](p5b-exit-r1-architecture.md)
- [R2 followup](p5b-exit-r2-followup.md)
- [R3 perf](p5b-exit-r3-perf.md)
- [R4 testing](p5b-exit-r4-testing.md)
- [R5 security](p5b-exit-r5-security.md)
- [R6 final verify](p5b-exit-r6-final-verify.md)
- [R7 final verify (after R6 fix)](p5b-exit-r7-final-verify.md)
