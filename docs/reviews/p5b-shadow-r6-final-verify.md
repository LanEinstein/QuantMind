### Verification Table
| # | Original Issue | Status | Notes |
|---|----------------|--------|-------|
| 1 | Debate transcript format mismatch | RESOLVED | `_join_debates` now emits `【看多研究员】` / `【看空研究员】`. |
| 2 | Concurrent shadow tasks racing budget guard | RESOLVED | Budget probe and baseline call are inside `_shadow_gate`. |
| 3 | Parse-failed legs skewed gate metrics | RESOLVED | `parse_ok=False` pairs are counted separately and excluded from gate math. |
| 4 | Routed parse failures always marked gateable | RESOLVED | `parse_ok` flows through fund manager → graph → collector → routed leg. |
| 5 | Missing Mongo burned baseline call | RESOLVED | `services.mongodb is None` is checked before budget/Kimi call. |
| 6 | Baseline parser stricter than live extractor | UNRESOLVED | Shared JSON extraction and numeric-string confidence are fixed, but parity is incomplete: live `_parse_signal` defaults missing `action` to `持有` with `parse_ok=True`, while baseline marks it `parse_ok=False`. |
| 7 | No-Mongo test missed `mongodb=None` | RESOLVED | `_DEFAULT_MONGO` sentinel makes explicit `None` reachable. |
| 8 | Unbounded shadow task backlog | RESOLVED | Admission cap plus baseline timeout are present. |
| 9 | Multi-pass aggregator + double sort | RESOLVED | Gate aggregation is consolidated; percentiles use one sorted list. |
| 10 | `_inflight_shadow` decrement not locked by tests | RESOLVED | Normal, exception, cancel/backlog paths assert counter release. |
| 11 | `parse_ok` propagation untested | RESOLVED | Wiring is present; direct fund-manager/collector check returns `False False`. The added full graph pytest timed out, as existing graph tests also do. |
| 12 | Timeout test wall-clock/no gate-release check | RESOLVED | Future-based timeout test verifies a second call proceeds. |
| 13 | Risk isolation redline transitively violated | RESOLVED | `backend.data.__init__` no longer eagerly imports; subprocess probe returned `[]`. |
| 14 | Sample-rate raw env value logged | RESOLVED | Log includes only `raw_type` and `raw_len`, not the env value. |

### New Critical Regressions (if any)
NONE

### Final Verdict
PARTIAL