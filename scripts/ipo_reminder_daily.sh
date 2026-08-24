#!/usr/bin/env bash
# MZ-1 institutional-rent ops — daily pre-open IPO/CB subscription reminder.
#
# Runs Mon-Fri 08:30 CST (subscription is a daytime action; the calendar for
# the day is published in advance). Silent when nothing is subscribable.
# Protocol: docs/research/institutional-rent-protocol-2026-08-23.md
#
# Red lines: official Tushare SDK only / display-only reminder (never an
# InstructionPlan) / zero LLM / a killed category stays killed until the
# owner clears data/institutional_rent/break_kill_state.json.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=/home/ps/anaconda3/envs/zhanglan/bin/python
export FEISHU_INTERACTIVE_ENABLED=false

# Credentials live in ~/.bashrc AFTER its interactive guard, so `source` is a
# NO-OP under cron — extract only the export lines we need (sleeve precedent).
if [ -z "${TUSHARE_TOKEN:-}" ] || [ -z "${FEISHU_APP_ID:-}" ]; then
  eval "$(grep -E '^export (TUSHARE_TOKEN|FEISHU_APP_ID|FEISHU_APP_SECRET|FEISHU_DECISION_CHAT_ID)=' "$HOME/.bashrc" || true)"
fi
for var in TUSHARE_TOKEN FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_DECISION_CHAT_ID; do
  if [ -z "${!var:-}" ]; then
    echo "[ipo-reminder] FATAL: $var is not set (checked env + ~/.bashrc exports)" >&2
    exit 3
  fi
done

mkdir -p logs
echo "[ipo-reminder] $(date -Is) run"
"$PY" scripts/push_ipo_reminder.py
echo "[ipo-reminder] $(date -Is) done"
