# I-002 Incident Playbook — Step-by-Step Failure Response

> Companion to [`i-002-production-runbook.md`](./i-002-production-runbook.md).
> Each section is a self-contained scenario the operator can follow
> when an alert lands in the Feishu alert chat or a metric drifts on
> the J-001 acceptance dashboard.
>
> Format per scenario: **Symptom → Detection → Diagnosis → Recovery
> → Verification → Post-mortem note**. Steps are validated against
> the J-005 N-day simulator harness — anything in this file must be
> replay-able without burning real LLM budget.

## Incident #1 — Backend will not start after env var edit

**Symptom.** `sudo systemctl restart quantmind` followed by `systemctl
status quantmind` shows `failed (Result: exit-code)`.

**Detection.** `journalctl -u quantmind -n 100 --no-pager` typically
contains one of:

- `OwnerProdAuthorizationError: ... missing`
- `OwnerProdAuthorizationError: ... expired (max 7 days)`
- `SecretsValidationError: Refusing to start ...`

**Diagnosis.**

```bash
cat /home/ps/.quantmind.env | grep -E "^(QUANTMIND_|FEISHU_INTERACTIVE_ENABLED|MONGODB|REDIS)" | grep -v "^#"
```

- Missing `QUANTMIND_PROD_RUN=1` or
  `QUANTMIND_OWNER_PROD_AUTHORIZATION=...` → fix per
  `i-002-production-runbook.md §11`.
- Authorization date > 7 days old → refresh.
- Malformed format → must match `^[A-Za-z0-9_-]+:\d{8}$`.

**Recovery.**

```bash
sudo nano /home/ps/.quantmind.env
# fix the line, save
sudo systemctl reset-failed quantmind
sudo systemctl restart quantmind
journalctl -u quantmind -n 20 --no-pager
```

**Verification.**

```bash
systemctl status quantmind  # expect "active (running)"
python scripts/smoke_test_cold_start.py --strict --json | jq '.verdict'
```

**Post-mortem.** Update the auth date in
`/home/ps/.quantmind.env` again proactively when the date is ≥6 days
old to avoid a same-day repeat.

## Incident #2 — Acceptance window resets unexpectedly

**Symptom.** J-001 dashboard shows `trading_days_in_window` dropped
from a non-zero value back to 1 between consecutive 16:30 checks.

**Detection.**

```bash
python scripts/acceptance_dashboard.py --json | \
    jq '.latest_reset_event'
```

The `trigger_type` field (one of the 5 P0-6 §1 values) explains
which trigger fired.

**Diagnosis.** Cross-reference with the audit trail:

```bash
python scripts/query_audit.py \
    --event-type system_interrupted \
    --since $(date -u -d "2 hours ago" +%Y-%m-%dT%H:%M:%SZ)
```

The matching audit event's `payload` carries `elapsed_seconds` +
`threshold_seconds` so you can confirm the trigger was legitimate
(e.g., a real 30-minute market data outage) vs a false positive
(e.g., a `MOCK_BROKER_CORRUPTION` driven by transient Mongo lag).

**Recovery.** See the per-trigger response in
`i-002-production-runbook.md §4`. The window will rebuild from the
reset moment forward; no manual intervention required to "un-reset".

**Verification.** Next 16:30 check should show
`trading_days_in_window=2`, then 3, … indicating the window is
walking forward cleanly without further triggers.

**Post-mortem.** If the same trigger fires twice in a 7-day window,
escalate: either upstream provider is chronically unstable (consider
provider switchover §6), or there's a logic bug in the detector
(file an incident).

## Incident #3 — Mongo replica set lost primary

**Symptom.** `journalctl -u quantmind` shows repeated
`pymongo.errors.NotPrimaryError` or
`broker_event_store_append_failed`.

**Detection.**

```bash
mongosh --quiet --eval "rs.status()" | jq '.members[] | {name, stateStr}'
```

**Diagnosis.** If no member is `PRIMARY`, the replica set lost
quorum.

**Recovery.**

```bash
# If the loss is transient (single-secondary outage), wait 30s for
# automatic re-election:
sleep 30
mongosh --quiet --eval "rs.status()" | jq '.members[] | {name, stateStr}'

# If still no PRIMARY, force a re-config (requires majority quorum):
mongosh --quiet <<'EOF'
cfg = rs.conf()
// Promote member 0 to primary
cfg.members[0].priority = 10
rs.reconfig(cfg, {force: true})
EOF
```

Restart the backend after primary is elected:

```bash
sudo systemctl restart quantmind
```

**Verification.** `scripts/smoke_test_cold_start.py --strict --json |
jq '.verdict'` should be `"PASS"` with the broker scheduler RS gate
passing.

**Post-mortem.** Log the timestamp + duration in the incident
register so the 16:00 acceptance compute's data_missing_rate
contribution is auditable.

## Incident #4 — Redis crashloop after host reboot

**Symptom.** `redis-cli ping` hangs or returns connection refused.
`systemctl status redis` shows `failed`.

**Detection.**

```bash
journalctl -u redis -n 100 --no-pager
```

Common root cause after reboot: AOF file corruption (rare but
documented for sudden power loss).

**Diagnosis.**

```bash
redis-check-aof /var/lib/redis/appendonly.aof
```

**Recovery.** If AOF is corrupted at a known offset:

```bash
sudo systemctl stop redis
redis-check-aof --fix /var/lib/redis/appendonly.aof
# Restart
sudo systemctl start redis
redis-cli ping  # expect PONG
```

If unrecoverable, restore from the latest RDB:

```bash
sudo systemctl stop redis
sudo cp /var/lib/redis/dump.rdb.<timestamp>.bak /var/lib/redis/dump.rdb
sudo systemctl start redis
```

Backend may need a restart so its `cost_probe` re-aggregates the
spend counters cleanly:

```bash
sudo systemctl restart quantmind
```

**Verification.** Cost dashboard reflects today's spend within ~5
minutes of restart:

```bash
curl -s http://127.0.0.1:8000/api/cost/breakdown | jq '.data.spent_breakdown.daily.total'
```

**Post-mortem.** Redis loss is **not** an acceptance window reset.
The metric counters in Mongo are authoritative; Redis cost-counter
drift is bounded to the prior RDB save interval (≤300s per the
default).

## Incident #5 — LLM provider returning 503 cascade

**Symptom.** Feishu alert chat lights up with
`llm_all_providers_failed` warnings. AnalysisScheduler `journalctl`
shows fast/slow cycles consistently timing out.

**Detection.**

```bash
journalctl -u quantmind --since "1 hour ago" | grep -E "provider_failed|llm_call_timeout"
```

Most often signals one or two providers down, not all three.

**Diagnosis.** Cross-check provider status pages. Run a
single-provider probe:

```python
# From a Python REPL with the backend env loaded
from openai import AsyncOpenAI
import asyncio, os

async def probe():
    for name, model in [
        ("deepseek", "deepseek-chat"),
        ("dashscope", "qwen-turbo"),
        ("moonshot", "moonshot-v1-8k"),
    ]:
        key = os.environ.get(f"{name.upper()}_API_KEY")
        client = AsyncOpenAI(api_key=key, base_url=f"https://api.{name}.com/v1")
        try:
            resp = await client.chat.completions.create(
                model=model, messages=[{"role":"user","content":"ping"}], max_tokens=8,
            )
            print(name, "OK", resp.choices[0].message.content)
        except Exception as e:
            print(name, "FAIL", e)

asyncio.run(probe())
```

**Recovery.** Two options:

1. **Wait it out.** The auto-fallback chain handles 1-of-3 outages.
   Acceptance counter not affected.
2. **Switch provider for an agent.** Edit
   `config/agent_models.yaml` to remove the down provider from the
   tiered chain, commit + write amendment doc, restart.

**Verification.** Acceptance dashboard's `llm_timeout_rate` metric
trends back below 0.05 within 1 hour.

**Post-mortem.** If a single provider has > 4 hours total outage in
a 45-day window, the `llm_timeout_rate` may breach 0.05 and FAIL the
gate. Document in the incident register and consider a longer
warm-up before the next 45-day cycle.

## Incident #6 — Daily ¥20 cost cap breached mid-day

**Symptom.** Feishu alert
`daily_cost_ceiling_20cny_breached` fires at 14:23. AnalysisScheduler
log shows `cost_guard_circuit_breaker_engaged`.

**Detection.**

```bash
curl -s http://127.0.0.1:8000/api/cost/breakdown | jq '.data.spent_breakdown.daily'
```

`total` field exceeds ¥20.

**Diagnosis.** Which agent over-spent?

```bash
curl -s http://127.0.0.1:8000/api/cost/breakdown | \
    jq '.data.spent_breakdown.daily.by_agent | to_entries | sort_by(-.value)'
```

Typically `fund_manager` with Kimi escalation enabled.

**Recovery.**

- **Daily hard cap is final** — no operator override per P1-7. LLM
  calls remain blocked until 00:00 Asia/Shanghai reset.
- The backend remains up; only LLM calls degrade. Non-LLM cron jobs
  (snapshots, EOD persistence) continue.
- The 1-hour LLM-stop window triggers J-004's `LLM_FULL_STOP_1H`
  reset trigger — confirm with `acceptance_dashboard.py`.

**Verification.** Next 00:00 reset:

```bash
# 00:00:05 Asia/Shanghai
curl -s http://127.0.0.1:8000/api/cost/breakdown | jq '.data.spent_breakdown.daily.total'
# Expect 0.0
```

**Post-mortem.** Tune the soft-degrade thresholds in
`backend/services/soft_degrade_manager.py` if Kimi escalation is the
chronic culprit. Any change is an amendment to P1-7.

## Incident #7 — Reconciliation ticket aging past 24h

**Symptom.** J-001 dashboard `reconciliation_ticket_open_count > 0`.
Feishu decision chat shows an unanswered reconciliation ticket from
yesterday's 16:00 reconciliation.

**Detection.**

```bash
curl -s http://127.0.0.1:8000/api/reconciliation-tickets | \
    jq '.data.tickets[] | select(.status == "OPEN")'
```

**Diagnosis.** Reconciliation freeze PAUSES the acceptance window —
the longer it persists, the longer the 45-day clock effectively
stalls. P0-5 enforces the freeze; no auto-resolution.

**Recovery.** Owner must decide via the API:

```bash
curl -X POST http://127.0.0.1:8000/api/reconciliation-tickets/<id>/decide \
    -H "Content-Type: application/json" \
    -d '{"decision": "RESOLVED_BROKER_AS_TRUTH"}'
```

(or `RESOLVED_USER_AS_TRUTH` / `AMENDED_WITH_SNAPSHOT` depending on
the underlying mismatch).

**Verification.**

```bash
python scripts/acceptance_dashboard.py | grep -A1 "LATEST ACCEPTANCE"
# outcome should flip from PAUSED back to INSUFFICIENT_DATA or
# PASS/FAIL depending on window state.
```

**Post-mortem.** If freezes occur frequently, review the freeze
source: data-quality breach? broker-mirror divergence? Each freeze
source has its own follow-up — see CLAUDE.md §2.4 5-source freeze
contract.

## Incident #8 — Disk full on the journald volume

**Symptom.** `systemctl status quantmind` shows `Failed to write to
journal: No space left on device`.

**Detection.**

```bash
df -h /var/log
journalctl --disk-usage
```

**Diagnosis.** The acceptance window does not depend on journald
storage; the backend continues to write `logs/quantmind.jsonl` and
`logs/audit.jsonl`. But operator visibility degrades.

**Recovery.**

```bash
# Vacuum aggressively
sudo journalctl --vacuum-time=14d
# Or by size
sudo journalctl --vacuum-size=500M
```

Long-term: tighten `/etc/systemd/journald.conf` SystemMaxUse +
SystemMaxFileSize per `systemd-setup.md §6`. Reload:

```bash
sudo systemctl restart systemd-journald
```

**Verification.**

```bash
journalctl -u quantmind -n 10 --no-pager
```

**Post-mortem.** Schedule a cron to vacuum monthly:

```cron
0 4 1 * * root journalctl --vacuum-time=30d
```

## Incident #9 — Holidays.yaml out of date in mid-year

**Symptom.** A normal Chinese holiday is treated as a trading day.
The 16:00 EOD chain fires on a closed-market day, producing a noisy
acceptance report.

**Detection.**

```bash
# On the unexpected trading-day notification:
python -c "
from datetime import date
from backend.data.trading_calendar import is_trading_day
print(is_trading_day(date(2026, 5, 1)))  # should be False (劳动节)
"
```

**Diagnosis.** `config/holidays.yaml` `holidays_<YEAR>` block is
missing or under-populated for the current year.

**Recovery.**

```bash
sudo nano config/holidays.yaml
# Add the missing dates per the 国务院办公厅 notice; bump
# schedule_version + last_verified.
sudo systemctl restart quantmind
# Calendar is loaded once at boot; restart is required (no hot reload
# per P0-6 §1).
```

**Verification.** Same `is_trading_day` probe now returns False.

**Post-mortem.** Set a calendar reminder for late November of each
year to update the next year's `holidays_<YEAR>` block before
December 31.

## Incident #10 — Operator typo locks the auth env var

**Symptom.** Owner edits `/home/ps/.quantmind.env` and accidentally
saves with `QUANTMIND_OWNER_PROD_AUTHORIZATION=alice 20260517`
(space instead of colon). Backend restart fails with
`malformed — value does not match`.

**Detection.**

```bash
journalctl -u quantmind -n 30 --no-pager | grep OwnerProdAuthorizationError
```

**Diagnosis.** Format is `^[A-Za-z0-9_-]+:\d{8}$`. Any deviation
(missing colon, ISO date, whitespace inside owner_identifier)
fails.

**Recovery.**

```bash
sudo nano /home/ps/.quantmind.env
# Fix to: QUANTMIND_OWNER_PROD_AUTHORIZATION=alice:20260517
sudo systemctl reset-failed quantmind
sudo systemctl restart quantmind
```

**Verification.** `OWNER_PROD_AUTHORIZATION_GRANTED` audit event
appears in `logs/audit.jsonl`:

```bash
python scripts/query_audit.py \
    --event-type owner_prod_authorization_granted --limit 1 --json
```

**Post-mortem.** The format error is forgiving (reject + clear
message) — no production damage. Consider adding `set -e` + a
pre-edit `grep` to a wrapper script if typos are recurrent.

## Incident #11 — Feishu long-connection drops for 3.5h

**Symptom.** Operator does not receive expected Feishu execution
prompts. Backend `journalctl` shows
`feishu_longconn_disconnected` warnings repeating.

**Detection.**

```bash
journalctl -u quantmind --since "4 hours ago" | grep -E "feishu_longconn|long_conn"
```

**Diagnosis.** WebSocket dropped at T-3.5h. Auto-reconnect cycle
visible in the logs but not succeeding.

**Recovery.** Below the 4h `LONG_CONN_OUTAGE_4H` threshold, no reset
fires yet. Manual intervention to avoid the threshold:

```bash
# Force a reconnect by restarting the backend (lifespan re-binds)
sudo systemctl restart quantmind
journalctl -u quantmind -f | grep feishu_longconn_connected
```

If Feishu OpenAPI is genuinely down, no client-side restart helps —
the reset trigger will fire at the 4h mark. Plan to ride out the
window reset.

**Verification.**

```bash
curl -s http://127.0.0.1:8000/api/system/status | \
    jq '.data.feishu.long_conn_state'
# expect "connected"
```

**Post-mortem.** Long-connection drops > 1h should be reviewed for
network root cause (host firewall, ISP route flap). The 4h threshold
is permissive precisely because brief drops are normal.

## Incident #12 — Acceptance gate stuck at FAIL after a metric breach

**Symptom.** Acceptance window completed 45 days but outcome is
`FAIL`. J-001 dashboard shows one or two metrics breached.

**Detection.**

```bash
python scripts/acceptance_dashboard.py --json | \
    jq '.latest_report.metrics[] | select(.passed == false)'
```

**Diagnosis.** A FAIL needs the offending metric to fall out of the
rolling 45-day window before the gate flips back. Identify the
worst-offending day:

```bash
mongosh --quiet --eval "
db.acceptance_reports.find({}, {trade_date:1, outcome:1, metrics:1})
  .sort({trade_date:-1}).limit(50).toArray()
" | jq
```

**Recovery.** Two paths:

1. **Wait out the window** — the bad day rolls off when 45 fresh
   trading days have accumulated since the breach.
2. **Force a reset** — if the breach was due to a known one-time
   incident (e.g., a deploy bug), record an explicit reset via the
   J-004 detector trigger after fixing the underlying cause; the
   window restarts.

**Verification.** Next 16:00 compute either:
- Stays at FAIL with the same gate breached (path 1, no progress).
- Drops to INSUFFICIENT_DATA with `trading_days_in_window=1` (path
  2, fresh window).

**Post-mortem.** Document which path was chosen and why; future
operators reading the incident log understand the trade-off (force
reset = 45 more days of waiting; wait it out = preserve the spend
already invested).

## Incident #13 — Off-host backup sync failed silently

**Symptom.** Weekly off-host archive cron (per
`i-002-production-runbook.md §5`) does not produce a new file.

**Detection.**

```bash
ls -ltr /off-host/quantmind/weekly/ | tail -5
journalctl -u quantmind-offsite-sync -n 50 --no-pager
```

**Diagnosis.** Common causes: rsync ssh key expired, off-host volume
full, network split.

**Recovery.**

```bash
# Manual run
sudo /usr/local/bin/quantmind-offsite-sync.sh

# If ssh key issue:
ssh-keygen -y -f /root/.ssh/quantmind_backup_key
# Re-add to off-host authorized_keys if needed
```

**Verification.** Off-host directory has a new `.tar.gz` matching
today's date.

**Post-mortem.** Add a Mongo backup-failed alert to the H-004 ALERT_MATRIX (`backup_failed` already exists; verify the cron writes
through it on next failure).
