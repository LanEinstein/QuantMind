### Verification Table
| # | Original Issue | Status | Notes |
|---|----------------|--------|-------|
| 1 | Parse persisted ISO timestamps for latency gates | RESOLVED | `_coerce_datetime` accepts tz-aware `datetime` and ISO strings, rejects naive values. |
| 2 | Treat missing cost telemetry as no-data | RESOLVED | Dates absent from Redis totals are dropped, so missing telemetry does not become zero-cost data. |
| 3 | Filter analysis records by requested window | RESOLVED | CLI uses `$or` window filter for BSON dates and ISO strings, with projection. |
| 4 | Look up `has_data` by full gate name | RESOLVED | `_GATE_TO_DATA_KEY` maps full shadow gate names correctly. |
| 5 | `split_runs_by_category` KeyError on missing `run_id` | RESOLVED | Missing/empty `run_id` records are dropped; `_cost_for` is defensive. |
| 6 | Partial cost telemetry could pass on covered subset | UNRESOLVED | Fast/slow cost gates fail closed, but `daily_total` can still report data/pass when one category is covered and another category is missing telemetry. |
| 7 | Markdown row broken by literal pipes | RESOLVED | `\|Δ\|` is escaped in the exit-gate markdown row. |
| 8 | CLI loaded full `analysis_records` | RESOLVED | Mongo query projects only required scalar fields. |
| 9 | `shadow_decisions` had no indexes | RESOLVED | `run_id` unique index and `created_at` index are created. |
| 10 | CLI live input path untested | RESOLVED | Live-input test mocks Mongo/Redis and asserts projection, window filter, and closes. |
| 11 | `_coerce_leg` accepted invalid actions / non-bool flags | RESOLVED | Action set, bool flags, numeric fields, and non-empty model are validated. |
| 12 | `shadow_decisions` index regression not locked | RESOLVED | Test asserts both required indexes, including TTL. |
| 13 | `shadow_decisions` retained indefinitely | RESOLVED | `created_at` index has `expireAfterSeconds=30*86400`. |
| 14 | Unbounded `--days` and JSONL | RESOLVED | Both CLIs bound `--days` to `[1,30]`; JSONL has a 200k-line cap. |
| 15 | `trade_date` markdown injection | RESOLVED | Non-`YYYY-MM-DD` trade dates are dropped before rendering. |

### New Critical Regressions (if any)
NONE

### Final Verdict
PARTIAL — issue #6 remains incomplete, but no new P1 regressions found.

Focused pytest could not run in this sandbox because Python reported no usable temporary directory.