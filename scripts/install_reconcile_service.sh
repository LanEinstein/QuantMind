#!/usr/bin/env bash
# MI-1 — install the quantmind-reconcile systemd unit (listener service).
#
# Usage:
#   sudo bash scripts/install_reconcile_service.sh --enable --start
#   bash scripts/install_reconcile_service.sh --dry-run     # no root needed
#
# Prerequisite: /home/ps/.quantmind-reconcile.env exists (chmod 600) with
# FEISHU_APP_ID/APP_SECRET/VERIFY_TOKEN/ENCRYPT_KEY/DECISION_CHAT_ID/
# OWNER_OPEN_ID + DEEPSEEK_API_KEY/DASHSCOPE_API_KEY — generate it as the
# owner user with:
#   bash scripts/install_reconcile_service.sh --write-env
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$REPO_DIR/deploy/quantmind-reconcile.service"
UNIT_DST="/etc/systemd/system/quantmind-reconcile.service"
ENV_FILE="/home/ps/.quantmind-reconcile.env"
ENV_VARS=(
  FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_VERIFY_TOKEN FEISHU_ENCRYPT_KEY
  FEISHU_DECISION_CHAT_ID FEISHU_OWNER_OPEN_ID
  DEEPSEEK_API_KEY DASHSCOPE_API_KEY MOONSHOT_API_KEY
)

DRY_RUN=false; ENABLE=false; START=false; WRITE_ENV=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --enable) ENABLE=true ;;
    --start) START=true ;;
    --write-env) WRITE_ENV=true ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if $WRITE_ENV; then
  # Owner-side (no root): extract the export lines from ~/.bashrc. Never
  # overwrites an existing file (it may hold hand-edited secrets).
  if [ -f "$ENV_FILE" ]; then
    echo "$ENV_FILE already exists — not overwriting"; exit 0
  fi
  pattern="$(IFS='|'; echo "${ENV_VARS[*]}")"
  grep -E "^export ($pattern)=" "$HOME/.bashrc" | sed 's/^export //' > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  count=$(wc -l < "$ENV_FILE")
  echo "wrote $ENV_FILE ($count vars, chmod 600)"
  if [ "$count" -lt 8 ]; then
    echo "WARNING: expected >=8 vars — check ~/.bashrc exports" >&2
  fi
  exit 0
fi

if $DRY_RUN; then
  echo "[dry-run] would copy $UNIT_SRC -> $UNIT_DST"
  echo "[dry-run] would systemctl daemon-reload"
  $ENABLE && echo "[dry-run] would systemctl enable quantmind-reconcile"
  $START && echo "[dry-run] would pkill any manual listener, then systemctl start quantmind-reconcile"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "root required (use --dry-run to preview)" >&2; exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE — run as the owner first:" >&2
  echo "  bash scripts/install_reconcile_service.sh --write-env" >&2
  exit 3
fi

install -m 644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
echo "installed $UNIT_DST"

$ENABLE && systemctl enable quantmind-reconcile && echo "enabled at boot"
if $START; then
  # A manually-started listener would double-reply to every owner
  # message — stop it before the service takes over (single-instance).
  pkill -TERM -f "scripts/reconcile_listener.py" 2>/dev/null || true
  sleep 2
  systemctl start quantmind-reconcile
  systemctl --no-pager --lines 0 status quantmind-reconcile || true
fi
