# Systemd Setup Runbook — J-003

> Production deployment of the QuantMind backend behind the
> `quantmind.service` unit. Pairs with the J-007 owner production-run
> gate and the J-006 I-002 production runbook.

## 1. Prerequisites

- `mongod` and `redis` installed and managed by systemd (the unit's
  `Requires=mongod.service redis.service` ordering depends on both
  being native systemd services, not docker-compose containers).
- Conda env `zhanglan` provisioned at
  `/home/ps/anaconda3/envs/zhanglan` with the project's
  `requirements*.txt` installed.
- Owner secrets staged in `~/.bashrc` per [P1-6
  §1.1](../decisions/P1-6.md) — the install script will lay down an
  empty `/home/ps/.quantmind.env` template; you fill it from the
  shell env.

## 2. One-shot install

```bash
sudo bash scripts/install_quantmind_service.sh --enable
```

The script:

1. Creates the unprivileged `quantmind` system user + group (idempotent).
2. Copies `deploy/quantmind.service` to `/etc/systemd/system/`.
3. Lays down `/home/ps/.quantmind.env` (chmod 600, owned by
   `quantmind:quantmind`) **only if absent** — never overwrites
   existing secrets.
4. Runs `systemctl daemon-reload`.
5. With `--enable`, runs `systemctl enable quantmind` (does not
   start; you do that manually after editing the env file).

Use `--dry-run` (no root required) to preview what the script will
do without applying. See `scripts/install_quantmind_service.sh
--help` for full flags.

## 3. Fill in the env file

Edit `/home/ps/.quantmind.env` and uncomment the secrets per the
template in `deploy/quantmind.env.example`. At minimum:

```
DEEPSEEK_API_KEY=sk-...
DASHSCOPE_API_KEY=sk-...
MOONSHOT_API_KEY=sk-...
MONGODB_URI=mongodb://localhost:27017/quantmind
REDIS_URL=redis://localhost:6379/0
```

When activating I-002 (production long-run), add the J-007 gate
variables:

```
QUANTMIND_PROD_RUN=1
QUANTMIND_OWNER_PROD_AUTHORIZATION=alice:20260517
```

(date must be no more than 7 days old or the service SystemExit's at
startup — see `backend/services/owner_authorization.py`).

Reload after edits: `sudo systemctl daemon-reload` (the unit reads
the env file at every start, so a plain `systemctl restart quantmind`
is enough).

## 4. Start + verify

```bash
sudo systemctl start quantmind
sudo systemctl status quantmind             # should show "active (running)"
journalctl -u quantmind -f                  # tail logs
curl http://127.0.0.1:8001/api/system/status  # backend health
```

Expected `journalctl -u quantmind` output during a clean cold start
(per `backend/main.py` lifespan):

1. `secrets_validator_ok` — fingerprints loaded
2. `owner_authorization_ok` (or `_skipped` for dev mode)
3. `mongo_connected` + `redis_connected`
4. `orchestration_layer_initialized`

If any step fails, the unit exits non-zero and systemd surfaces the
failure via `systemctl status`. The `Restart=always` + `RestartSec=10s`
config retries up to 20 times in 5 minutes
(`StartLimitIntervalSec=300` + `StartLimitBurst=20`); after the burst
limit trips the unit goes `failed` and requires manual intervention:

```bash
sudo systemctl reset-failed quantmind
sudo systemctl restart quantmind
```

## 5. Graceful restart / stop

```bash
sudo systemctl restart quantmind
sudo systemctl stop quantmind
```

The unit sets `KillMode=mixed` + `KillSignal=SIGTERM` +
`TimeoutStopSec=60s` so the lifespan `finally` arm has time to
- stop `BrokerScheduler` (flushes any in-flight broker events),
- close the Mongo + Redis clients,
- flush the audit JSONL.

If shutdown exceeds the 60s grace period systemd issues SIGKILL.

## 6. journald + log retention

journald keeps logs in `/var/log/journal/`. The default retention is
controlled by `/etc/systemd/journald.conf`. Recommended for
QuantMind:

```ini
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=90day
```

Apply: `sudo systemctl restart systemd-journald`. The structured
audit trail (`logs/audit.jsonl`) is kept by the backend at 30-day
rolling per P1-6 §1.7.4 — journald is the operator-facing surface,
audit.jsonl is the forensic source of truth.

## 7. Time sync

Acceptance window arithmetic (P0-6) + the 5 reset trigger thresholds
(J-004) rely on accurate wall-clock. Verify systemd-timesyncd or
chronyd is active:

```bash
timedatectl status     # check "System clock synchronized: yes"
sudo systemctl enable --now systemd-timesyncd
```

For multi-host deploys, point timesyncd at the same NTP pool as
mongod and redis so the audit timestamps cross-correlate cleanly.

## 8. SIGTERM auto-restart drill

After install, exercise the restart contract once:

```bash
sudo systemctl start quantmind
sleep 5
sudo systemctl status quantmind | grep "active (running)"  # confirm up
sudo pkill -TERM -f "uvicorn backend.main:app"             # simulate crash
sleep 12                                                    # 10s RestartSec + grace
sudo systemctl status quantmind | grep "active (running)"  # back up
```

A drift between expected and observed restart behaviour means the
unit file is mis-installed; re-run the installer.

## 9. Crashloop debugging

If the service repeatedly crashes within 5 minutes, the
`StartLimitBurst=20` cap engages and systemd marks it `failed` with
"start-limit-hit". Inspect:

```bash
journalctl -u quantmind -n 200 --no-pager
systemctl status quantmind
```

Typical root causes:

- Missing `/home/ps/.quantmind.env` (or wrong chmod) →
  `EnvironmentFile` line in unit fails.
- Owner authorization expired or absent →
  `OwnerProdAuthorizationError` SystemExit.
- mongod / redis not started → `Requires=` brings them up, but if
  they themselves crashloop the dependency cascade looks identical to
  a QuantMind crash.
- Conda env path drift → `ExecStart` cannot find the uvicorn binary.

Reset + retry after fix:

```bash
sudo systemctl reset-failed quantmind
sudo systemctl restart quantmind
```

## 10. quantmind-reconcile — the MI-1 listener unit (2026-08-24)

A separate, lightweight unit for the MI-1 reconcile listener
(`scripts/reconcile_listener.py`: owner Feishu free text → mirror
ledger → renderer ack). No mongod/redis/uvicorn — the heavy
`quantmind.service` backend stays dormant.

Install (two steps — env as the owner, unit as root):

```bash
bash scripts/install_reconcile_service.sh --write-env     # once, no root
sudo bash scripts/install_reconcile_service.sh --enable --start
```

The installer refuses to run without `/home/ps/.quantmind-reconcile.env`
(9 vars: 6×FEISHU_* + 3 LLM keys, extracted from `~/.bashrc`, chmod
600) and, on `--start`, first SIGTERMs any manually-started listener so
two instances never double-reply to the same owner message.

Verify / operate:

```bash
systemctl status quantmind-reconcile          # active (running)
journalctl -u quantmind-reconcile -f          # canonical log tail
sudo systemctl restart quantmind-reconcile    # after a code update
```

Crash recovery mirrors the backend unit: `Restart=always` + 10s,
20-restart/5-min crashloop cap, graceful SIGTERM stop. Runs as
`User=ps` deliberately — the mirror ledger / push state / logs are
ps-owned files shared with the cron pipeline (a dedicated service user
would only add chown gymnastics on shared data).

## 11. Uninstall

```bash
sudo systemctl stop quantmind
sudo systemctl disable quantmind
sudo rm /etc/systemd/system/quantmind.service
sudo systemctl daemon-reload
# Optional: remove the service user (preserves /home/ps/.quantmind.env
# for re-install; delete that manually if you want a full wipe).
sudo userdel quantmind
```
