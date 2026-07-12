#!/usr/bin/env bash
# SLV-1 trial ops — daily post-close pipeline (owner-authorized 2026-07-12).
#
# Runs Mon-Fri after Tushare daily data lands (~17:00 CST):
#   1. incremental PIT ingest (idempotent skip-if-present; a holiday ingests 0);
#   2. forward survival runner (pre-registered kill-switch; ACCRUING < 8 periods);
#   3. display-only Feishu advisory push (locally deduped per as-of date).
#
# Red lines: official Tushare SDK only / byte-exact PIT store / zero LLM /
# display-only advisory (never an InstructionPlan) / a kill-switch breach shows
# status=KILLED in the push — the owner stops manually (fail-closed reporting).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=/home/ps/anaconda3/envs/zhanglan/bin/python
export FEISHU_INTERACTIVE_ENABLED=false

# Credentials live in ~/.bashrc AFTER its interactive guard, so `source ~/.bashrc`
# is a NO-OP under cron (codex finding). Extract only the export lines we need —
# never execute the whole bashrc from a non-interactive shell.
if [ -z "${TUSHARE_TOKEN:-}" ] || [ -z "${FEISHU_APP_ID:-}" ]; then
  eval "$(grep -E '^export (TUSHARE_TOKEN|FEISHU_APP_ID|FEISHU_APP_SECRET|FEISHU_DECISION_CHAT_ID|FEISHU_ALERT_CHAT_ID)=' "$HOME/.bashrc" || true)"
fi
for var in TUSHARE_TOKEN FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_DECISION_CHAT_ID; do
  if [ -z "${!var:-}" ]; then
    echo "[sleeve-trial] FATAL: $var is not set (checked env + ~/.bashrc exports)" >&2
    exit 3
  fi
done

# The cron line redirects into logs/ — make sure it exists for manual runs too.
mkdir -p logs

TODAY=$(date +%Y%m%d)
# Self-healing ingest window: resume from the LAST stored `daily` trade date
# (idempotent re-fetch of that day is skipped), so an outage of ANY length
# backfills completely instead of leaving silent holes (codex finding: a fixed
# 7-day lookback would permanently gap the forward series after >7-day downtime).
LAST_STORED=$("$PY" - <<'PYEOF'
import json
last = ""
with open("data/marketdata_pit/index.jsonl", encoding="utf-8") as fh:
    for line in fh:
        if '"endpoint": "daily"' not in line and '"endpoint":"daily"' not in line:
            continue
        rec = json.loads(line)
        if rec.get("endpoint") == "daily":
            last = max(last, str(rec.get("trade_date", "")))
print(last or "20260615")
PYEOF
)

echo "[sleeve-trial] $(date -Is) ingest ${LAST_STORED}..${TODAY} (resume from last stored)"
"$PY" scripts/ingest_historical_pit.py \
  --start "$LAST_STORED" --end "$TODAY" \
  --snapshot-root data/marketdata_pit --with-coverage

echo "[sleeve-trial] $(date -Is) forward runner"
"$PY" -m scripts.factor_research.defensive_sleeve_forward

echo "[sleeve-trial] $(date -Is) feishu push"
"$PY" scripts/push_sleeve_advisory.py

echo "[sleeve-trial] $(date -Is) done"
