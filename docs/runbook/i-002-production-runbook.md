# I-002 Production Runbook — 45-Trading-Day Acceptance Long-Run

> **Owner-only operations guide.** I-002 is the multi-week long-run
> that burns real LLM budget. Read this end-to-end before passing
> `QUANTMIND_OWNER_PROD_AUTHORIZATION` to the systemd unit. Pair with
> [`i-002-incident-playbook.md`](./i-002-incident-playbook.md) for
> failure-mode response.

## §1. Pre-flight checklist

Before pasting the J-007 authorization env var into
`/home/ps/.quantmind.env`, confirm every box below.

- [ ] **Phase J 7/7 complete.** All tasks `done` in
  `docs/plan.html#tasks` (`J-001` through `J-007`).
- [ ] **Last `scripts/smoke_test_cold_start.py` was PASS** within the
  last 24 hours; `verdict: PASS` in the captured output.
- [ ] **Last `scripts/simulate_n_trading_days.py --days 45` was PASS**
  within the last 7 days; `verdict: PASS` in the captured output. No
  callback errors, no reset triggers fired.
- [ ] **Mongo and Redis are systemd-managed** (not docker-compose);
  `systemctl status mongod redis` both `active (running)`.
- [ ] **`/home/ps/.quantmind.env`** exists with `chmod 600`, owned by
  `quantmind:quantmind`, and contains the three LLM keys + the four
  storage URIs.
- [ ] **`systemd-timesyncd`** active and `timedatectl status` shows
  `System clock synchronized: yes`.
- [ ] **Backup volume mounted** at the path referenced by the nightly
  Mongo dump (default `/var/backups/quantmind/`) with `>= 20G` free.
- [ ] **Alert chat reachable** — `FEISHU_ALERT_CHAT_ID` open in your
  Feishu client and you can manually post a test message. Every J-004
  reset and J-001 acceptance flip lands here.
- [ ] **Owner authorization staged** in
  `/home/ps/.quantmind.env`:
  `QUANTMIND_PROD_RUN=1` + `QUANTMIND_OWNER_PROD_AUTHORIZATION=<owner>:YYYYMMDD`
  with today's date (max 7 days old).

If **any** box is unchecked, do not start the long-run. Re-run the
relevant Phase J task or fix the prerequisite first.

## §2. Cold-start sequence

Production cold start is strictly ordered. Out-of-sequence starts
(e.g., backend before redis) trigger systemd's `Requires=` cascade
which mostly works but leaves transient errors in `journalctl`.

```bash
# 1. Mongo (must be replica-set primary for P1-2.A multi-doc tx)
sudo systemctl start mongod
mongosh --quiet --eval "rs.status().ok"   # expect "1"

# 2. Redis (AOF + RDB persistence per §7)
sudo systemctl start redis
redis-cli ping                            # expect "PONG"

# 3. Backend
sudo systemctl start quantmind
journalctl -u quantmind -f                # tail the lifespan
```

Expected journalctl pattern within the first 60 seconds:

```
secrets_validator_ok credential_count=8 warning_count=0
owner_authorization_ok owner_identifier=<owner> granted_date=YYYY-MM-DD
mongo_connected uri=mongodb://localhost:27017/quantmind
redis_connected url=redis://localhost:6379/0
orchestration_layer_initialized feishu_client=True decision_chat_wired=True
brokerscheduler_started crons=4
analysis_scheduler_started mode=fast_slow
```

If any of those lines is missing or replaced with a `*_error`
counterpart, **stop immediately**:

```bash
sudo systemctl stop quantmind
```

and consult §10 of the incident playbook.

## §3. Daily 16:30 acceptance verification

Every weekday at 16:30 (Asia/Shanghai) the BrokerScheduler `advance_day`
cron has fired, the 16:00 `eod_pipeline` has written today's
`AcceptanceReport`, and the J-001 dashboard reflects the new row.

Operator routine:

```bash
# 1. Pull the latest acceptance snapshot
python scripts/acceptance_dashboard.py --json | tee /tmp/today.json

# 2. Verify outcome
jq '.latest_report.outcome' /tmp/today.json
# Expected during warm-up: "INSUFFICIENT_DATA"
# After 45 trading days clean: "PASS"
# Any FAIL → investigate which gate(s) tripped (see §6 + §8)

# 3. Verify trading_days_in_window incremented by 1 since yesterday
jq '.latest_report.trading_days_in_window' /tmp/today.json
```

If `trading_days_in_window` did **not** increment, either:

- The window was reset by a J-004 trigger (check `latest_reset_event`).
- The EOD pipeline did not complete (check `journalctl` for
  `eod_pipeline_freeze_state_active`).
- The day was a holiday or weekend (verify in `config/holidays.yaml`).

Cross-check the projected PASS date:

```bash
jq '.projection' /tmp/today.json
```

Volatility hint: if the projected date keeps slipping forward by more
than 1 trading day per real day, the window is resetting frequently
— investigate the reset trigger frequency.

## §4. The 5 reset trigger response manual

Per [P0-6 §1](../decisions/P0-6.md), five system-level interruptions
reset the 45-day acceptance counter. Reconciliation freeze is **not**
a reset (it pauses without zeroing).

### 4.1 MARKET_DATA_OUTAGE_30MIN

**Detection.** Primary + backup quote feed both stale for ≥30 min.

**Counter zeroed?** Yes — J-004 detector clamps `window_start` to the
detection timestamp at next `AcceptanceService.compute()`.

**Operator steps.**

1. Check upstream provider status pages (akshare upstream, sina,
   tencent finance, eastmoney).
2. `journalctl -u quantmind | grep -E "market_data|MARKET_DATA"` for
   the last 1 hour to see the staleness trend.
3. If the provider outage continues, **do not restart** the backend;
   that won't fix upstream. Wait for resolution.
4. After resolution, the backend resumes pulling fresh quotes
   automatically. Verify with
   `python scripts/acceptance_dashboard.py` that the next 16:00
   compute lands on `INSUFFICIENT_DATA` outcome rather than another
   reset.

### 4.2 LLM_FULL_STOP_1H

**Detection.** All 3 LLM providers (DeepSeek + Qwen + MiniMax / Kimi)
returning errors / timeouts for ≥1 hour.

**Counter zeroed?** Yes.

**Operator steps.**

1. Check provider dashboards: deepseek.com, dashscope.console.aliyun.com,
   platform.moonshot.cn.
2. Try the manual switchover path in §6 — switching to a healthy
   provider can short-circuit the trigger.
3. If all three providers are genuinely down, the audit + Feishu alert
   has already fired. Decision: **pause the long-run** (stop the
   backend) until at least one provider recovers, OR ride it out and
   accept the window reset.

### 4.3 MOCK_BROKER_CORRUPTION

**Detection.** Checksum mismatch on the broker hybrid-delta + EOD
snapshot recovery path (P1-2.A).

**Counter zeroed?** Yes, and importantly, **DO NOT restart the
backend until the corruption source is diagnosed**.

**Operator steps.**

1. Stop the backend: `sudo systemctl stop quantmind`.
2. Dump the broker collections for forensic inspection:
   ```
   mongodump --db quantmind --collection broker_events
   mongodump --db quantmind --collection broker_snapshots
   ```
3. Compare the snapshot checksum vs the replayed event sequence.
4. If a single event is malformed, surgically remove it from
   `broker_events` (back up first!), restart the backend with
   `QUANTMIND_BROKER_SKIP_RS_GATE=1` only as a dev escape — never in
   production. Production must always run with the gate ON.
5. After restart, monitor for 1 trading day before considering the
   window's reset resolved.

### 4.4 STATE_MACHINE_ILLEGAL_TRANSITION

**Detection.** InstructionPlan state machine observes a forbidden
transition (e.g., DISPATCHED → VALIDATED).

**Counter zeroed?** Yes.

**Operator steps.**

1. The audit payload (search `journalctl` for the trigger event ID)
   identifies the offending `instruction_id`, `from_state`, and
   `to_state`.
2. Pull the full state history for the instruction from MongoDB:
   `db.instruction_plans.find({instruction_id: "QM-..."}).pretty()`.
3. File the incident; this is typically a logic bug in
   `backend/services/instruction_plan_builder.py` or
   `backend/broker/appliers.py` that needs a code fix.
4. If reproducible, do **not** restart production until the bug is
   patched — repeated triggers will keep zeroing the counter.

### 4.5 LONG_CONN_OUTAGE_4H

**Detection.** Feishu lark-oapi WebSocket dropped for ≥4 hours after
the overlay is enabled.

**Counter zeroed?** Yes.

**Operator steps.**

1. Check Feishu service status at <https://open.feishu.cn/document/>.
2. Restart the long-connection client by restarting the backend
   (graceful — the orchestrator re-binds on lifespan startup).
3. If the WebSocket cannot re-establish, fall back to the
   simulation_auto path temporarily — disable
   `FEISHU_INTERACTIVE_ENABLED` and restart. This pauses the human-in-
   loop overlay without losing the acceptance window (note: the
   window is already reset; the failover prevents a *second* reset
   when the next interval check fires).

### 4.6 Reconciliation freeze (NOT a reset)

P0-6 §1 explicitly excludes reconciliation freeze from the 5 resets.
A `RECONCILIATION_TICKET_OPEN` state PAUSES the acceptance window
(today's report comes back `outcome=PAUSED`) without zeroing the
counter. The window resumes the moment the operator decides the
ticket via `POST /api/reconciliation-tickets/{ticket_id}/decide`.

## §5. Mongo backup cadence (P1-2.A)

P1-2.A locks the persistence model as **hybrid delta + EOD snapshot**.
Operator nightly backup mirrors that:

| Cadence | Command | Retention |
|---------|---------|-----------|
| Hourly | `mongodump --db quantmind --out /var/backups/quantmind/hourly/$(date +%Y%m%d-%H)` | 48h |
| Nightly | `mongodump --db quantmind --gzip --out /var/backups/quantmind/nightly/$(date +%Y%m%d)` | 30 days |
| Weekly | full archive of nightly into off-host storage | 365 days |

Cron stanza (`/etc/cron.d/quantmind-backup`):

```
0 * * * * quantmind /home/ps/papers/QuantMind/scripts/backup.sh hourly
30 16 * * 1-5 quantmind /home/ps/papers/QuantMind/scripts/backup.sh nightly
0 4 * * 0 root /usr/local/bin/quantmind-offsite-sync.sh
```

Recovery validation drill (run quarterly):

```bash
mongorestore --drop --gzip /var/backups/quantmind/nightly/<date>/
# Then run scripts/smoke_test_cold_start.py against the restored DB
# to verify the orchestration layer comes up clean.
```

## §6. Redis persistence strategy

Redis carries hot caches (price ladder, cost spend counters, dedup
windows). Persistence is dual-mode:

- **RDB snapshot** every 5 minutes when ≥1 key changed (`save 300 1`).
- **AOF append-only** with `everysec` fsync — bounded data loss in a
  crash to ≤1 second of writes.

`/etc/redis/redis.conf` excerpt:

```
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
dir /var/lib/redis
```

Verify after install:

```bash
redis-cli CONFIG GET appendonly         # expect "yes"
redis-cli CONFIG GET appendfsync        # expect "everysec"
redis-cli LASTSAVE                       # last RDB snapshot epoch
```

A Redis loss is **not** an acceptance-window reset — the durable
truth is in Mongo. Restart redis and the backend auto-warms its
caches from Mongo on demand. Cost-spend counters can drift by up to
1 second of pre-crash spend; the cost_guard `cost_probe` reconciles
on the next aggregation tick.

## §7. LLM provider switchover path

The router config (`config/agent_models.yaml`) wires three providers
in a tiered fallback chain. Operator override paths in priority order:

1. **Provider-level outage** — DeepSeek down. The router's automatic
   fallback to Qwen kicks in. No operator action needed, but verify
   in `journalctl`:
   ```
   primary_provider_failed agent_name=<x> provider=deepseek
   trying_fallback_provider agent_name=<x> fallback_provider=qwen
   ```
2. **Tiered escalation outage** — Kimi escalation unavailable. The
   H-003 `SoftDegradeManager` sets a Redis flag that vetoes Kimi
   specifically; DeepSeek + Qwen continue to serve every request. No
   operator action needed.
3. **Two providers down** — manual switch the affected agent in
   `config/agent_models.yaml` to use the surviving provider, restart
   the backend (`config` is read once at boot per P0-7 / P0-10 /
   P1-7 — no hot reload). Audit the change with a commit + amendment
   doc before restart.
4. **All three down** — the J-004 `LLM_FULL_STOP_1H` trigger will
   fire at 1 hour cumulative. Decision: pause the long-run and wait,
   or accept the reset.

## §8. Cost overrun handling (P1-7)

Three independent budget guards:

| Guard | Threshold | Action on breach |
|-------|-----------|------------------|
| Daily hard cap | ¥20 / day | Full LLM circuit breaker (1h reset trigger candidate). Resets at 00:00 Asia/Shanghai. |
| Monthly soft | ¥440 / month | 50% / 80% / 100% audit-only milestones. No service interruption. |
| Kimi daily cap | ¥4 / day | Kimi escalation blocked; DeepSeek + Qwen continue. |

Operator response:

```bash
# Current spend snapshot
curl -s http://127.0.0.1:8000/api/cost/breakdown | jq

# Today's hourly trend
curl -s http://127.0.0.1:8000/api/cost/breakdown | \
    jq '.data.spent_breakdown.daily.hourly'
```

If the daily ¥20 hard cap trips, an audit event +
`acceptance_reset_triggered` Feishu alert fire simultaneously. The
backend pauses LLM calls for the rest of the day; the next 00:00
reset resumes service. There is **no operator override** for the
daily hard cap — re-arming requires a P1-7 amendment + restart.

## §9. Accidental restart recovery (P1-2.A)

If the backend is force-killed (SIGKILL, OOM, host crash) within the
45-day window:

1. The `BrokerEventStore.recover_state` + `MockBroker.seed_from_recovery`
   chain auto-rebuilds the broker mirror from `broker_events` +
   `broker_snapshots` (≤3 hour replay window per P1-2.A).
2. The in-memory `WindowResetState` on `AcceptanceService` is lost —
   if a J-004 trigger had fired before the crash, its in-process
   record is gone. The audit trail in `audit_events` (Mongo +
   JSONL) preserves the trigger event, but the next `compute()` will
   NOT clamp the window unless someone replays the trigger via the
   J-001 dashboard's reset event view.
3. Manually re-apply if needed:
   ```python
   # Inside a Python REPL with the lifespan up
   from datetime import UTC, datetime
   svc = app.state.acceptance_service
   svc.record_reset(
       when=datetime(2026, 5, 17, 10, 0, tzinfo=UTC),
       reason="LLM_FULL_STOP_1H",
   )
   ```
   Then trigger the 16:00 acceptance recompute manually:
   `await app.state.broker_scheduler.run_eod_pipeline()`.

For a clean restart (operator initiated `systemctl restart
quantmind`), the recovery path is automatic and idempotent — confirm
by re-running `scripts/smoke_test_cold_start.py` after the restart
to verify the 18 orchestration slots populate.

## §10. Stopping the long-run

Stopping the long-run is a deliberate operation. The 45-day window
**does not** pause when the backend stops — every calendar trading
day that passes without a fresh AcceptanceReport still counts as
"data missing" for the data_missing_rate metric, eventually dragging
the rolling average below the 0.99 threshold and FAILing the gate.

Decision tree:

- **Pause < 1 day** (e.g., for an emergency patch + restart): no
  acceptance impact. Counter unaffected.
- **Pause 1–3 days**: data_missing_rate begins to drift. Document
  the pause in `docs/runbook/incident-log.md` so the operator can
  exempt the affected days from the metric calculation.
- **Pause ≥ 3 days**: practically forces the window to reset
  (data_missing_rate breach + FAIL outcome at the next 16:00). Plan
  for a full 45-day re-run.

```bash
# Graceful stop (SIGTERM + 60s grace per the unit's TimeoutStopSec)
sudo systemctl stop quantmind

# Confirm clean exit in the journal
journalctl -u quantmind -n 50 --no-pager | tail -20
```

After resumption:

```bash
sudo systemctl start quantmind
python scripts/smoke_test_cold_start.py     # verify clean boot
python scripts/acceptance_dashboard.py      # verify counter state
```

## §11. Authorization expiry mid-run

`QUANTMIND_OWNER_PROD_AUTHORIZATION` is checked **only at startup**.
A 7-day-old authorization that was valid when the backend started
remains in force for the running process; the gate only triggers on
the next boot.

If the backend crashes and systemd tries to restart with an expired
auth (`StartLimitBurst=20` retries kick in), every restart will
SystemExit with `OwnerProdAuthorizationError`. The unit goes
`failed`. Owner refreshes:

```bash
# Edit the env file — update the YYYYMMDD to today's date
sudo nano /home/ps/.quantmind.env

# Reset + restart
sudo systemctl reset-failed quantmind
sudo systemctl restart quantmind
journalctl -u quantmind -n 20 --no-pager
```

The new `OWNER_PROD_AUTHORIZATION_GRANTED` audit event lands in
`logs/audit.jsonl` and Mongo `audit_events` on successful boot.

## §12. End-of-run procedure

When the 45-day window completes with `outcome=PASS`:

1. **Verify** via the J-001 dashboard:
   ```bash
   python scripts/acceptance_dashboard.py | tee /tmp/final.txt
   grep "outcome.*PASS" /tmp/final.txt
   ```
2. **Cross-check** with the audit trail:
   ```bash
   python scripts/query_audit.py \
       --event-type system_interrupted \
       --since $(date -u -d "45 days ago" +%Y-%m-%dT%H:%M:%SZ) \
       --until $(date -u +%Y-%m-%dT%H:%M:%SZ) | head -50
   ```
   Should show no `acceptance_reset_trigger` rows in the window
   ending at PASS.
3. **Owner decision** — toggle `FEISHU_INTERACTIVE_ENABLED=true` in
   `/home/ps/.quantmind.env` AND `systemctl restart quantmind`. The
   lifespan re-runs the P0-6 §2 gate; on PASS it activates the
   long-connection receiver.
4. **Archive** the full audit trail for the 45-day window into
   off-host storage. The window remains in Mongo for 180 days per
   P1-6 §1.7.4 TTL.

## §13. References

- [P0-6](../decisions/P0-6.md) — acceptance window definition.
- [P1-2.A](../decisions/P1-2.A.md) — persistence + recovery.
- [P1-6](../decisions/P1-6.md) — secrets + audit + 127.0.0.1.
- [P1-7](../decisions/P1-7.md) — cost budget.
- [systemd-setup.md](./systemd-setup.md) — unit install + journalctl.
- [secrets-incident-response.md](./secrets-incident-response.md) —
  credential leak playbook.
- [i-002-incident-playbook.md](./i-002-incident-playbook.md) —
  step-by-step failure responses.
