# Production-Hardening Audit — Session Handoff (2026-06-23)

> **READ THIS FIRST if you are the next session continuing the production-hardening
> sweep.** This is the authoritative continuation pointer for the owner-directed
> "全面审视 → 找短板/隐藏bug → 专业修复 → codex review → 达实战水平" task.
> Owner steer (2026-06-23): **全修 5-9**, then handoff. This doc captures the
> remaining work so you can execute it without re-auditing.

---

## 0. Honest framing (do NOT regress this)

- This is a **simulation + Feishu-manual-execution** research platform. **永禁真实
  券商程序化下单** — there is no "real automated trading" go-live state.
- "实战水平" here = the **unattended simulation-autopilot** runs for days/weeks
  across open/close/holidays/restarts without crashing, wedging, silently
  corrupting state, or mis-acting. That is the lens for every fix.
- **Profitability is NOT certified and must not be claimed.** It is gated on the
  QGR forward-confirmation (B-layer), which has not happened. Engineering
  hardening ≠ profitable strategy. The CLAUDE.md/README rewrite stays **deferred**
  until QGR forward-confirmation passes (owner decision 2026-06-23). Do not write
  docs claiming "expected stable profitability".

## 1. What this session did (DONE + PUSHED to origin/main)

A full read-only audit (7 parallel agents over backend/frontend/scripts, lensed
for unattended-sim robustness) → then 4 safety-critical batches fixed, each
TDD'd + codex-reviewed + committed. **`origin/main` is at `d2edb88`.**

| Commit | Batch | What |
|--------|-------|------|
| `0c4d648` | gate fix | added `__init__.py` to 6 `tests/` subdirs — full `pytest` was RED at collection (duplicate `test_import_isolation.py` basename). |
| `da6b7f4` | **B1** | wired the daily-loss −5% brake (MTM-NAV) + order-count cap to live data; `RiskConfig.max_total_positions` default 10→5. Amendment `P0-7-amendment-2026-06-23-risk-control-live-wiring.md`. |
| `2ce846e` | **B2(calendar)** | fail-closed holiday-calendar staleness guard (can't boot/trade an un-curated year); EOD job holiday-gated. Amendment `P0-6-amendment-2026-06-23-...`. |
| `531a4c1` | **B3** | T+1 recovery correctness (B3/S3: `recover_state(now=)` re-derives `today_bought_volume` from `bought_by_date`) + fail-closed cash underflow (B4). |
| `d2edb88` | **B4(data)** | `fina_indicator_vip` pagination (D2) + IPv4-egress enforced for data SDKs (D6). Amendment `P0-8-amendment-2026-06-23-data-layer-ipv4-and-staleness-deferral.md`. |

Baseline after the sweep: **6900 passed / 14 skipped / 90% cov**, `ruff` clean,
`scripts/redline-check.sh` EXIT=0.

## 2. Execution protocol (follow exactly — this is what worked)

Per batch: **investigate (read real code, verify the finding) → write amendment
FIRST if it touches a decision boundary (§1.5) → TDD (test first) → run targeted
tests + `ruff` → full suite → codex/code-review → fix all findings → full suite +
`redline-check` → commit (one cohesive commit per batch, no attribution).**

- **Codex CLI is effectively unusable this session** (`codex review --uncommitted`
  hangs/times out — `gpt-5.5` at `xhigh` + the codebase-memory MCP startup is too
  slow). **Use the `/code-review high` skill instead** (per memory
  `feedback_codex_rate_limit_fallback`). It launches finder agents and **caught a
  real bug in EVERY batch** — keep using it. Run it foreground; for the diff it
  reviews the uncommitted working tree (`git diff HEAD`).
- **Defer, don't rush, anything that needs new infra or empirical validation on
  the trading/durable-ledger path.** A rushed "fix" there can be a worse bug
  (e.g. a mis-parsed staleness clock that spuriously halts ALL trading). This
  session deferred 4 such items (§4) into tracked tasks rather than risk it.
- **Gate commands:**
  ```bash
  PY=/home/ps/anaconda3/envs/zhanglan/bin
  FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q                 # full suite (~2min, 6900+)
  FEISHU_INTERACTIVE_ENABLED=false $PY/pytest tests/test_X.py -q # targeted
  $PY/ruff check backend/ tests/ scripts/
  bash scripts/redline-check.sh                                  # EXIT=0 required
  cd frontend && npm run type-check && npm run test -- --run && npm run build  # Batch 8
  ```
- **Ops gotchas:** full pytest MUST set `FEISHU_INTERACTIVE_ENABLED=false`. Avoid
  `cd` in compound bash (permission prompt) — use absolute paths. Edits to a file
  require a prior Read-tool read of it. **Push is owner-gated** — commit locally,
  do not push without explicit owner authorization.

## 3. REMAINING WORK — Batches 5-9 (execute these; findings are pre-verified file:line)

> Severity P0>P1>P2. class `PURE_BUG` = fix directly. class `BOUNDARY` = write an
> amendment first (`docs/decisions/*-amendment-2026-06-DD-{reason}.md`), then fix.
> Every item below was surfaced by an audit agent and grounded in real code; still
> Read + confirm before editing.

### Batch 5 — scheduler hardening (all PURE_BUG; S2 is the valuable one)
| id | sev | finding | file:line | fix |
|----|-----|---------|-----------|-----|
| **S2** | P1 | 30s market job makes UNBOUNDED blocking vendor calls (no `asyncio.wait_for`) → a hung adata/akshare socket silently wedges market-data collection (`max_instances=1` drops every later tick). | `backend/data/scheduler.py:215-225` (`_run_market_job`) + `backend/data/market_data.py` fetch helpers | wrap the per-tick fetch in `asyncio.wait_for(..., timeout≈20s)` so a hung tick fails closed + frees the slot (mirror the Line-2 intraday tick-timeout). |
| S6 | P2 | 30s `IntervalTrigger` jobs inherit `misfire_grace_time=1` → any >1s loop stall silently drops the MTM/intraday tick. | `backend/broker/scheduler.py:398-405,453-460` + `backend/data/scheduler.py:122-136` | set explicit `misfire_grace_time≈30` on the interval jobs. |
| S8 | P2 | process-down at a cron instant is silently skipped — no missed-run watchdog (a missed 16:00 EOD = no freeze, no alert). | `backend/broker/scheduler.py:388` (in-memory jobstore) + absent liveness cron | startup check "an EOD snapshot exists for the last completed trading day" → SCHEDULER alert if not (extend AA-003 catch-up idea to EOD). |
| S5 | P2 | 6 `*_CRON` class constants are 6-field; `CronTrigger.from_crontab` would raise if ever wired (currently dead — inline 5-field strings are used). | `backend/broker/scheduler.py:214-257` | make the constants the 5-field SSoT and reference them in `add_job`, or delete the dead constants. |
| S9 | P2 | fire-and-forget `asyncio.create_task(self._run_catch_up(...))` handle not retained (GC risk; not cancelled on stop). | `backend/data/analysis_scheduler.py:209` | store the handle on `self`, cancel/await it in `stop()`. |
| S7 | P2 | `run_shortlist` docstring + `agents_team/CLAUDE.md` §5 still say "one debate per daily shortlist" — contradicts the live per-candidate model. | `backend/agents_team/graph.py:203-217` | update docstring + module CLAUDE.md to the per-candidate model. |
| S4 | P2 | fan-out gate counters rely solely on Redis UTC-date TTL; the documented "00:00 reset cron" does NOT exist (harmless today, misleading invariant). | `backend/services/cost_guard.py:731-769` | either register the 00:00 reset cron OR correct the docstrings to say per-UTC-date keys are the reset mechanism. |

### Batch 6 — monitoring & value-factor (M1 is a PROVEN correctness bug)
| id | sev | finding | file:line | fix | class |
|----|-----|---------|-----------|-----|-------|
| **M1** | P1 | quality factor reads `gross_margin` (absolute gross profit in **yuan**) instead of `grossprofit_margin` (the **ratio**) → cross-sectional GPM ranks by company SIZE = the spurious size-tilt QGR rejected. **PROVEN against the raw payload** (002210.SZ: gross_margin=4.3e7 vs grossprofit_margin=0.61). Masked because test fixtures seed the wrong column. | `backend/fundamentals_pit/reader.py:259,285` | change `fields=["roe","gross_margin"]`→`grossprofit_margin` and `announced_values(code,"gross_margin")`→`grossprofit_margin`; fix `tests/value_assembly/test_assembler.py:87` + `tests/fundamentals_pit/test_reader.py:80,103` fixtures; add an assertion GPM is a ratio not 1e8. (Dormant — value sleeve is env-OFF — but wrong the moment it activates.) | PURE_BUG |
| M3 | P2 | the deterministic 4-condition anti-martingale ADD (`evaluate_add_intents`) is wired only into tests, no production runner. | `backend/monitoring/add_position.py:394` | wire it into the daily runner (deriving `regime`) OR annotate test-only/deferred so the dormancy is explicit. | PURE_BUG(dead) |
| M4 | P2 | an interior 0/negative close inflates the baseline σ → can MASK (never fabricate) a real anomaly (fail-safe direction). | `backend/monitoring/anomaly.py:191-193` (`_returns`) | screen the full `closes` series for non-positive interior bars in `scan()` (→ NO_PRICE), matching the screener. | PURE_BUG(hygiene) |
| M5 | P2 | RSI docstring claims "Wilder-style" but computes a simple average. | `backend/monitoring/add_position.py:185-196` | rename docstring to "simple-average RSI" OR implement Wilder smoothing. | PURE_BUG(doc) |
| M2 | P2 | backend value PIT cutoff is INCLUSIVE (`ann_date<=as_of`) while the research PIT is strictly EXCLUSIVE (`<decision`) → same-day look-ahead vs the project's own standard. | `backend/screening/value_factors.py:255` | make backend strict (`>=as_of → continue`) to match research, OR amendment-document the inclusive choice if the Line-1 frame is post-close. | BOUNDARY (amendment) |

### Batch 7 — cost & broker concurrency/parity (all P2)
| id | finding | file:line | fix | class |
|----|---------|-----------|-----|-------|
| D5 | `¥20`/`¥14` docstring drift after the `¥100`/`¥70` amendment (~14 stale refs) — operator-misleading. | `backend/services/cost_guard.py:12-16` + `cost_probe.py:46-50` + `soft_degrade_manager.py` + `agents_team/graph.py:210/221` | sweep `¥20→¥100`, `¥14→¥70` in docstrings/comments (docs-only). | PURE_BUG(doc) |
| D4 | actual LLM spend reconciled vs the cap only post-hoc, not vs the released reservation → `¥100` cap = "no NEW call once over" not "total never exceeds" (worst overshoot ≈ one call). | `backend/services/cost_guard.py:1051-1064` (`settle_budget`) + `backend/llm/router.py:489-497` | at settle, true-up by `(actual − estimate)` when actual>estimate (or reserve `max(estimate, actual)`). | PURE_BUG(minor) |
| B8 | at-fill price-limit recheck uses `round(...,2)` (banker's) while RiskEngine uses Decimal `ROUND_HALF_UP` → boundary divergence (spurious at-fill rejects on `…x5` cents). | `backend/broker/mock_broker.py:428-446` vs `backend/risk/engine.py:61-81` | share one `price_limit_ceiling(prev_close, board)` helper between both; add a parity test. | PURE_BUG |
| B5 | executor reads `get_trades()[-1]` across a lock gap → a concurrent fill can misattribute trade_id/economics. | `backend/services/simulation_executor.py:162-164` | have `place_order`/`OrderResult` return the `trade_id` directly so the executor never reads shared `_trades`. | PURE_BUG |
| B6 | `read_latest_sequence()+1` MAX-scan has no unique index on `sequence`/`event_id` → two concurrent appenders could collide on real Mongo. | `backend/broker/persistence/store.py:194-243` | declare a unique index on `sequence` (+ `event_id`) so a collision raises `DuplicateKeyError` and retries. | PURE_BUG(low) |
| B7 | EOD snapshot build reads account state and `last_event_sequence` non-atomically → a fill landing in the window can be double-counted on the next recovery. | `backend/broker/persistence/recovery.py:464-469` + `backend/broker/scheduler.py:1096-1157` | capture state + cursor under a single broker lock / freeze mutation for the snapshot-build critical section. | PURE_BUG(low) |

### Batch 8 — frontend robustness (F1-F4 are P1) + MANDATORY Playwright exam
Security posture is clean (no credential storage, no forbidden WS kinds, no 0.0.0.0). Findings:
| id | sev | finding | file:line | fix |
|----|-----|---------|-----------|-----|
| **F1** | P1 | WS reconnect resets backoff on every brief OPEN + has no attempt cap/jitter → reconnect storm vs a flaky endpoint on a days-open dashboard; no "permanently down" surfacing. | `frontend/src/composables/useWebSocket.ts:62-86,158-167` | only reset `reconnectAttempt` after a stability window (≥10s open); add jitter + a max-attempt cap that surfaces a persistent "实时连接断开" banner. |
| **F2** | P1 | the JS↔Python regex mirror is NEVER asserted byte-equal — a one-sided edit passes both suites; the UI banner overclaims "guaranteed in sync". | `frontend/src/utils/executionRegex.ts:5-13` + both specs + `tests/test_execution_regex_mirror_backend.py` | emit `PATTERNS_AS_DICT` to a generated JSON artifact + assert `PATTERN_STRINGS[id] === artifact[id]` in vitest (normalize `(?P<…>)`↔`(?<…>)`); soften the banner wording. |
| **F3** | P1 | live position/order/MTM tables crash-blank if a WS/API row omits a numeric field (bare `.toFixed`). | `frontend/src/components/trading/PositionTable.vue:36,41,70` (+ OrderList, Portfolio MTM) | coerce defensively `(row.x ?? 0).toFixed(2)` and/or validate the WS payload shape before committing to the store. |
| **F4** | P1 | account switch keeps the PREVIOUS account's cash/positions on screen under the new tab (permanently if the new fetch errors) → human could mis-execute on the wrong account. | `frontend/src/stores/portfolio.ts:144-147` | reset `account/positions/orders/trades/circuitBreakerStatus` at the top of `switchAccount`; clear (not retain) on a failed fetch. |
| F5 | P2 | SlotRotation/Thesis/AutopilotTimeline/DualLine panels fetch once on mount, never poll → look "live" but freeze. | those `*.vue` | add a `setInterval` poll (60s like ValueSleevePanel) + `onUnmounted(clearInterval)`. |
| F6 | P2 | `AccountBanner` "净值曲线(30日)" is a hardcoded sine wave, not real equity. | `frontend/src/components/trading/AccountBanner.vue:120-130` | feed from the real `equityPointsApi`, or relabel/remove. |
| F8 | P2 | `ManualTradeForm` SELL has no sellable cap when `sellableVolume` is null → over-sell into the ledger. | `frontend/src/components/trading/ManualTradeForm.vue:139-152` | enforce `volume<=maxVolume` in `canSubmit` for SELL; warn when sellable unknown. |
| F9 | P2 | `DualLineStatusPanel` dereferences nested `payload.line2.*` without guards (latent blank on partial payload). | `frontend/src/components/dashboard/DualLineStatusPanel.vue:17-57` | optional-chain each sub-object / normalize in `fetchStatus`. |
| F10 | P2 | `AutopilotTimeline` swallows backend outages (`.catch(()=>null)`) and renders them as a normal idle pipeline. | `frontend/src/components/dashboard/AutopilotTimeline.vue:170-179` | track per-source failure flags + render "数据获取失败" instead of idle. |
| F7 | P2 | `mintExternalTradeId` uses `Math.random()` (1000 buckets) → same code+side+second can collide + be deduped away. | `frontend/src/api/manualTrades.ts:39-40` | use `crypto.randomUUID()` slice / sub-second precision. |
| — | gate | **owner-mandated frontend exam** (plan.html AD lane): after `npm run build`, codex+Playwright full exam (功能/排版/美观/动画/图表实时/排布), Claude fixes, Claude re-tests via Playwright. This is the closure gate for Batch 8 (per `feedback_playwright_frontend_exam`). |

### Batch 9 — research/process (Sc1 is the valuable one)
| id | sev | finding | file:line | fix | class |
|----|-----|---------|-----------|-----|-------|
| **Sc1** | P1 | `redline-check.sh` is NOT wired into CI/pre-commit (only gitleaks; no `.github/`) — the 1731-line redline gate is manual-only + can be silently bypassed; the header even claims it "gate[s] CI". | `scripts/redline-check.sh:4` vs `.pre-commit-config.yaml` | add a `local` pre-commit hook (`entry: bash scripts/redline-check.sh`, `pass_filenames: false`, `always_run: true`) and/or a minimal CI job; correct the header. | PURE_BUG(process) |
| Sc4 | P2 | the `BT_FLOAT` decision-comparison redline regex only catches float literals, not integer thresholds (`if score>0:` slips). | `scripts/redline-check.sh:1712` | widen the regex to integers OR comment that integer thresholds are deliberately exempt. | PURE_BUG(minor) |
| Sc2 | P2 | CPCV embargo is sized EXACTLY equal to the max label horizon (20td) — zero margin; a cadence/horizon change silently reintroduces straddle leakage. | `scripts/factor_research/walk_forward_eval.py:49` + `cpcv.py:176-179` | derive embargo from `ceil(max_horizon/rebalance_freq)+1` and assert `embargo*freq >= max_label_horizon`. | BOUNDARY(latent) |

> Sc3 (IC t-stat on overlapping windows) is an already-DOCUMENTED caveat with the
> DSR/PBO/SPA arena as the real control — no action needed.

## 4. Deferred tasks (need dedicated care — do NOT fold into a quick batch)
These were carved out because a rushed fix risks a worse bug on the safety-critical path. Each needs its own amendment + careful TDD (and #12/#13 need empirical vendor-data validation):
- **#10 (B1b) realized-PnL-on-close + check-14 consecutive-loss streak** — the broker tracks NO realized per-trade PnL (`_apply_sell` discards cost basis; `get_account` is cost-based). Build realized-pnl-on-close (avg-cost − fees, emitted on the SELL `ORDER_FILLED` event) + last-N store + recovery, then wire check-14. The daily-loss −5% brake (done in B1) already caps daily damage.
- **#11 (B3b) sim-fill write-ahead durability (B2)** — `simulation_executor.place_order` mutates the broker BEFORE persisting `ORDER_PLACED/ORDER_FILLED`; a crash/Mongo-blip in that window loses the fill on restart. True fix = write-ahead + recovery handling of orphan ORDER_PLACED.
- **#12 (B4b) real exchange-clock staleness (D1)** — staleness gate measures fetch RTT not quote age. Needs a new `StockQuote.quote_time` field + validated vendor-timestamp parsing (sina has a DATE+TIME header; **adata exposes no timestamp column — verify**). A WRONG parse spuriously halts trading → needs empirical validation.
- **#13 (B4c) suspension detection in the quote probe (D3)** — probe hardcodes `is_suspended=False`; needs a `suspend_d` provider wired per-code (the existing `is_suspended` takes a `WatchlistMarketSnapshot`, not the probe's `StockQuote`).

## 5. Doc-drift list (for the eventual QGR-gated CLAUDE.md/README rewrite — NOT now)
When QGR forward-confirmation passes and the owner unblocks the doc rewrite, reconcile these stale statements (the CODE/gate is authoritative):
- CLAUDE.md §2.4/§2.11/§5 say "仅 2 写端点" → actual **3** (`manual-trades`, `P1-5-amendment-2026-06-12`, `redline-check.sh` allowlist=3).
- `FeishuMessageKind` 6→**7** (`manual_trade_recorded`).
- §2.2 "单调用 30s + 0 重试" → `llm/providers.py` actually uses `read=120s, max_retries=1` (verify whether amendment-backed; reconcile).
- cost_guard docstrings `¥20/¥14` → `¥100/¥70` (this is Batch-7 D5 — fix in code; CLAUDE.md §2.10 already records the amendment).
- `docs/plan.html` `AF-003` still `status="doing"` → it's done (`007d7f1`). Flip it.

## 6. Suggested next-session prompt
> 接手 QuantMind 实战化加固(production-hardening)第 2 程。先读
> `docs/handoff/production-hardening-handoff-2026-06-23.md`(本 session 已 push 到
> origin/main `d2edb88`,完成 Batch 1-4 安全关键修复)。按其 §2 执行协议
> (audit→amendment→TDD→/code-review high→commit;codex CLI 超时用 /code-review
> high;push 待我授权)**全修 Batch 5-9**(§3 findings 已 pre-verified file:line):
> Batch 5 调度器加固(S2 防卡死优先)→ Batch 6 监控/因子(M1 已证 GPM bug 优先)→
> Batch 7 成本/broker 并发 → Batch 8 前端(F1-4 + owner 强制 Playwright 体检)→
> Batch 9 研究/流程(Sc1 红线接 CI)。每批一 commit,跑全量 pytest(6900+ 基线)+
> ruff + redline-check 全绿再提交。盈利不认证(QGR-gated),文档重写仍暂缓。完成后
> 报告 + 等我授权 push。§4 的 4 个 deferred 任务(#10-13)不要塞进快批,各自需专项。
