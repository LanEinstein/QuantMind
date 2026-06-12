#!/usr/bin/env bash
# AB-003 / P2-2-amendment-2026-06-12 §1.2 — controlled activation restart.
#
# External supervisor lane (NOT runtime code): cron this at 08:30
# Asia/Shanghai on trading days. It restarts the backend ONLY when a
# staged next_boot.lock.json exists, inside the 08:25–08:45 window
# (>=65min before the 09:35 Line-1/Line-2 runs; the 2h pre-open
# blackout applies to STAGING the lock, which happens the prior
# evening — see backend/strategy_evolution/activation.py).
#
# The boot itself consumes the lock (apply_pending_activation):
# backup -> atomic lockfile swap -> registry health assert -> automatic
# rollback on failure. git mirrors the record after the fact (ops
# lane); this script never touches git.
#
# Example crontab (owner machine):
#   30 8 * * 1-5 /home/ps/papers/QuantMind/deploy/promote_restart.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEXT_BOOT_LOCK="${REPO_ROOT}/config/next_boot.lock.json"
SERVICE_NAME="${QUANTMIND_SERVICE_NAME:-quantmind-backend}"

now_hm="$(TZ=Asia/Shanghai date +%H%M)"
if [ "${now_hm}" -lt 0825 ] || [ "${now_hm}" -gt 0845 ]; then
  echo "promote_restart: outside the 08:25-08:45 window (${now_hm}); skip"
  exit 0
fi

if [ ! -f "${NEXT_BOOT_LOCK}" ]; then
  echo "promote_restart: no staged next_boot.lock.json; skip"
  exit 0
fi

echo "promote_restart: staged activation found — restarting ${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
echo "promote_restart: restart issued; boot consumes the lock (auto-rollback on health failure)"
