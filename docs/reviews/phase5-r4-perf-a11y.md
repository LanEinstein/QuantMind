**Findings**

| Severity | Location | Confidence | Issue | Fix |
|---|---:|---|---|---|
| High | [backend/data/database.py](/home/ps/papers/QuantMind/backend/data/database.py:446) | High | `count_signals_for_date()` and `count_signals_since()` query `trade_date` without the leading `stock_code` field, so the existing `(stock_code, trade_date)` index cannot efficiently serve monitoring dashboard counts. This can become a collection/index scan on every dashboard poll. | Add a standalone `trading_signals` index on `trade_date`, e.g. `[("trade_date", DESCENDING)]`, or change dashboard counts to use a query shape with `stock_code` if that is intentional. |
| Medium | [backend/data/database.py](/home/ps/papers/QuantMind/backend/data/database.py:417) | High | `/api/analysis/history` supports `stock_code`-only and `trade_date`-only filters, but the compound index `(stock_code, trade_date, created_at)` only covers the `created_at` sort when both fields are constrained. `stock_code`-only and `trade_date`-only queries can require in-memory sorting as records grow. | Add indexes for supported filter shapes, likely `(stock_code, created_at DESC)` and `(trade_date DESC, created_at DESC)`, or constrain the API to only sort-covered shapes. |
| Medium | [backend/services/analysis_stream.py](/home/ps/papers/QuantMind/backend/services/analysis_stream.py:147) | High | `_finalize()` silently drops the `None` close sentinel if a subscriber queue became full after accepting the terminal event. The stream loop does not break on terminal events itself, so non-frontend clients can heartbeat forever after receiving the terminal payload. | Guarantee sentinel delivery by draining or dropping one queued item before `put_nowait(None)`, or have `stream_analysis_job()` break after yielding `pipeline_completed` / `error`. |
| Medium | [backend/data/analysis_scheduler.py](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:233) | Medium | Catch-up startup does one `query_signals()` call per watchlist stock. With the watchlist bounded at 500, startup can issue hundreds of Mongo round-trips for a single date check. | Fetch today’s signals once with `{trade_date, stock_code: {$in: codes}}`, build a set, then diff against the watchlist. |
| Medium | [frontend/src/views/AgentDebate.vue](/home/ps/papers/QuantMind/frontend/src/views/AgentDebate.vue:66) | High | History search calls `store.fetchHistory` on every input event. This creates unnecessary API and DB traffic while the user types, even though non-6-digit name searches are filtered client-side. | Debounce the fetch, and only call the backend immediately for date changes or when the query becomes a valid 6-digit code; otherwise rely on `filteredHistory`. |
| Medium | [frontend/src/views/AgentDebate.vue](/home/ps/papers/QuantMind/frontend/src/views/AgentDebate.vue:240) | Medium | `isStreaming` is tied to `sseState.connected`. `useSSE` closes the EventSource on any error, so a transient transport drop marks streaming false while the backend job can still be running, allowing another expensive analysis job to start. | Track an `activeJob` / `running` state separate from transport connectivity and clear it only on terminal event, explicit cancel/reset, or confirmed failure. |
| Medium | [frontend/src/views/AgentDebate.vue](/home/ps/papers/QuantMind/frontend/src/views/AgentDebate.vue:73) | High | The `role="listbox"` container directly contains `role="status"`, `role="alert"`, and empty placeholder children in loading/error/empty states. A listbox should own options/groups, so screen readers can get invalid semantics in those states. | Render loading/error/empty outside the listbox, or only apply `role="listbox"` to the wrapper that contains actual `role="option"` rows. |
| Medium | [frontend/src/views/AgentDebate.vue](/home/ps/papers/QuantMind/frontend/src/views/AgentDebate.vue:288) | Medium | Starting or retrying analysis does not move focus to the live/streaming region, and the start button can become disabled while focused. Keyboard and screen-reader users may not land on the newly updating content. | Add a focusable main/streaming region (`tabindex="-1"`, labelled region) and focus it after starting; return focus to the retry/start action after failed retry paths where appropriate. |

**Checked With No Finding**

| Area | Result |
|---|---|
| SSE heartbeat | 15s is reasonable for common proxy idle timeouts and not overly chatty. |
| Stream buffer trimming | `del events[1:2]` is O(n), but the list is capped at 256, so the cost is bounded. |
| Subscriber broadcast | `for q in list(subscribers)` is fine for the expected one/few subscribers. |
| Mongo cursor bounds | New monitoring helpers use bounded `to_list(length=1)` or aggregate iteration; no unbounded cursor materialization found. |
| Redis publish/usage tracking | Pub/sub has no retention; usage keys have TTL. |
| Frontend debate updates | Array spreads are O(n), but debate rounds are capped small by `max_debate_rounds`; not a current hotspot. |
| Contrast | New history loading/error placeholders use existing dark-theme tokens and appear AA-safe. |
| Bundle loading | AgentDebate route is lazy-loaded; no new heavy startup import in the reviewed files. |

**Verdict**

Needs changes before clearing Round 4. The main blockers are the dashboard/history index gaps, the SSE terminal close edge case, and the a11y semantics/focus gaps in `AgentDebate.vue`.