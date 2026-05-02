# P5B Shadow-Runner Wiring — Codex Review Summary

**Scope**: backend/services/shadow_runner.py + integration into
analysis_scheduler + fund_manager parse_ok propagation
(fund_manager.py, graph.py, collector.py, records.py) +
shadow_compare gateable filter + admission control + agent_models.yaml
shadow baseline agent + risk-isolation regression test.

**Cycles**: R1 architecture/open → R2 followup → R3 perf → R4 testing
→ R5 security → R6 final verify (PARTIAL) → R7 final verify (PASS).

**Final Verdict**: ✅ PASS (R7 closure check, no regressions).

## Issues by round

| Round | Focus | P1 | P2 | P3/Other | Verdict |
|------:|-------|---:|---:|---------:|---------|
| R1 | architecture / open | 1 | 2 | 0 | NEEDS_FIXES |
| R2 | followup verification | 0 | 3 | 1 (P3) | NEEDS_FIXES |
| R3 | performance | 0 | 1 | 1 (P3) | NEEDS_FIXES |
| R4 | testing | 0 | 2 | 1 (P3) | NEEDS_FIXES |
| R5 | security | 1 (HIGH) | 0 | 1 (LOW) | NEEDS_FIXES |
| R6 | final verify | — | — | — | PARTIAL (1 unresolved) |
| R7 | final verify (R6 fix) | — | — | — | PASS |

## Issue ledger (14 total, all RESOLVED)

| # | Round | Severity | Issue | File |
|---|------|----------|-------|------|
| 1 | R1 | P1 | Debate transcript Bull:/Bear: vs 【看多研究员】/【看空研究员】 | shadow_runner.py |
| 2 | R1 | P2 | Concurrent shadow tasks racing budget guard | shadow_runner.py |
| 3 | R1 | P2 | Parse-failed legs skewed gate metrics | shadow_compare.py |
| 4 | R2 | P2 | Routed parse failures always marked gateable | fund_manager.py + graph.py + collector.py + records.py + shadow_runner.py |
| 5 | R2 | P2 | Missing Mongo burned baseline LLM call | shadow_runner.py |
| 6 | R2 | P2 | Baseline parser stricter than live extractor | shadow_runner.py |
| 7 | R2 | P3 | No-Mongo test missed mongodb=None | tests/test_shadow_runner.py |
| 8 | R3 | P2 | Unbounded shadow task backlog + no timeout | shadow_runner.py |
| 9 | R3 | P3 | Multi-pass aggregator + double sort | shadow_compare.py |
| 10 | R4 | P2 | _inflight_shadow decrement not regression-locked | tests |
| 11 | R4 | P2 | parse_ok end-to-end propagation untested | tests/test_agents_graph.py |
| 12 | R4 | P3 | Timeout test wall-clock + no gate-release verification | tests/test_shadow_runner.py |
| 13 | R5 | HIGH | Risk isolation redline transitively violated | backend/data/__init__.py + tests/test_risk_isolation_redline.py |
| 14 | R5 | LOW | Sample-rate raw env value logged | shadow_runner.py |
| (R6 follow-up) | R6 | UNRESOLVED→RESOLVED | Baseline parser missed default action parity | shadow_runner.py |

## Test baseline

- **1139 passed**, 11 skipped, 0 failed (vs P5B-exit harness 1077 → +62 net).
- Coverage: backend overall ~83%; backend/risk 98%;
  backend/services/shadow_runner has full unit coverage on every
  branch (env enable, sample rate, parse, gate, admission, timeout,
  budget breach, mongo absent, parser parity).
- New regression test `tests/test_risk_isolation_redline.py` runs in
  a fresh subprocess and asserts `import backend.risk.engine` does
  not transitively load any `backend.llm/agents/mirofish/services`.

## Round files
- [R1 architecture/open](p5b-shadow-r1-architecture.md)
- [R2 followup](p5b-shadow-r2-followup.md)
- [R3 perf](p5b-shadow-r3-perf.md)
- [R4 testing](p5b-shadow-r4-testing.md)
- [R5 security](p5b-shadow-r5-security.md)
- [R6 final verify (PARTIAL)](p5b-shadow-r6-final-verify.md)
- [R7 final verify (PASS)](p5b-shadow-r7-final-verify.md)
