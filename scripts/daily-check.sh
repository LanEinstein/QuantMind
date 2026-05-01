#!/usr/bin/env bash
#
# QuantMind daily operator check (evaluation-period).
#
# Collects: systemd backend state, docker compose infra, monitoring
# dashboard JSON, 24h log error counts, MongoDB collection cardinality,
# 3 most-recent backups from the repo-external backup directory. Output
# is tee'd to logs/daily-check-<date>.log for archival.
#
# Usage:
#   ./scripts/daily-check.sh                     # writes log to logs/
#   BASE_URL=https://quantmind.local ./scripts/daily-check.sh
#
# Exit code is 0 if no "critical" signal is detected, 1 otherwise.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/.local/state/quantmind/backups}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
DB_NAME="${DB_NAME:-quantmind}"

mkdir -p "$LOG_DIR"

date_tag="$(date -u +%Y%m%d)"
report_log="$LOG_DIR/daily-check-${date_tag}.log"

exit_code=0

exec > >(tee -a "$report_log") 2>&1

echo "================================================================"
echo " QuantMind daily check — $(date -Iseconds)"
echo " BASE_URL=$BASE_URL"
echo "================================================================"

section() { echo; echo "— $* —"; }

# 1. systemd backend ---------------------------------------------------------
section "systemd: quantmind-backend"
if command -v systemctl >/dev/null; then
    if systemctl list-unit-files quantmind-backend.service >/dev/null 2>&1; then
        systemctl is-active quantmind-backend && \
            echo "  active=ok" || { echo "  active=FAIL"; exit_code=1; }
        systemctl status quantmind-backend --no-pager --lines=3 | tail -4
    else
        echo "  (quantmind-backend.service not installed — see deploy/README.md)"
    fi
else
    echo "  systemctl unavailable (dev environment?)"
fi

# 2. docker compose infra ----------------------------------------------------
section "docker compose: mongodb + redis"
if command -v docker >/dev/null; then
    (cd "$ROOT" && docker compose ps --format 'table {{.Service}}\t{{.Status}}')
    # Verify healthy
    unhealthy=$(cd "$ROOT" && docker compose ps --format '{{.Service}}:{{.Health}}' | grep -Ev ':healthy|:$' || true)
    if [[ -n "$unhealthy" ]]; then
        echo "  WARN: unhealthy containers:"
        echo "$unhealthy"
        exit_code=1
    fi
else
    echo "  docker unavailable"
fi

# 3. monitoring dashboard ----------------------------------------------------
section "monitoring dashboard"
dashboard_json=$(curl -fsSL --max-time 10 "$BASE_URL/api/monitoring/dashboard" 2>/dev/null || true)
if [[ -n "$dashboard_json" ]] && command -v python3 >/dev/null; then
    echo "$dashboard_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print(f"  overall_status={d[\"overall_status\"]}")
print(f"  signals today/7d={d[\"signals\"][\"today\"]}/{d[\"signals\"][\"last_7_days\"]}")
print(f"  latest analysis lag_seconds={d[\"analysis\"][\"lag_seconds\"]}")
print(f"  cost today={d[\"cost\"][\"today_cny\"]}/{d[\"cost\"][\"daily_budget_cny\"]} over_budget={d[\"cost\"][\"over_budget\"]}")
print(f"  llm providers available={d[\"llm\"][\"available_count\"]}/{len(d[\"llm\"][\"providers\"])}")
print(f"  risk circuit_breaker={d[\"risk\"][\"circuit_breaker\"]}")
sys.exit(0 if d["overall_status"] != "critical" else 2)
'
    rc=$?
    if [[ $rc -eq 2 ]]; then exit_code=1; fi
else
    echo "  dashboard unreachable at $BASE_URL/api/monitoring/dashboard"
    exit_code=1
fi

# 4. 24h error / warning counts ----------------------------------------------
section "logs: 24h error/warning counts"
if [[ -d "$LOG_DIR" ]]; then
    err_count=$(find "$LOG_DIR" -name '*.log' -mtime -1 -print0 2>/dev/null | \
        xargs -0 grep -icE '\[error\]|level.{0,5}error' 2>/dev/null | \
        awk -F: '{s+=$NF} END{print s+0}')
    warn_count=$(find "$LOG_DIR" -name '*.log' -mtime -1 -print0 2>/dev/null | \
        xargs -0 grep -icE '\[warn\]|level.{0,5}warn' 2>/dev/null | \
        awk -F: '{s+=$NF} END{print s+0}')
    echo "  errors(24h)=$err_count  warnings(24h)=$warn_count"
else
    echo "  log dir missing: $LOG_DIR"
fi

# 5. MongoDB collection sizes ------------------------------------------------
section "mongodb collection sizes"
if command -v docker >/dev/null && (cd "$ROOT" && docker compose ps --services --filter status=running | grep -q '^mongodb$'); then
    (cd "$ROOT" && docker compose exec -T mongodb mongosh "$DB_NAME" --quiet --eval '
        ["trading_signals", "analysis_records", "news_articles", "cost_tracking"]
          .forEach(c => print(`  ${c}=${db[c].estimatedDocumentCount()}`));
    ')
else
    echo "  mongodb container not running"
fi

# 6. Recent backups ----------------------------------------------------------
section "recent backups (last 3)"
if [[ -d "$BACKUP_DIR" ]]; then
    ls -1t "$BACKUP_DIR"/mongodump-*.gz 2>/dev/null | head -3 | while read -r f; do
        size=$(stat -c '%s' "$f" 2>/dev/null)
        echo "  $(basename "$f") (${size} bytes)"
    done || echo "  no backups found"
else
    echo "  backup dir missing: $BACKUP_DIR"
fi

# 7. Summary -----------------------------------------------------------------
section "summary"
if [[ $exit_code -eq 0 ]]; then
    echo "  status=OK"
else
    echo "  status=WARN (see sections above)"
fi
echo "  report saved to $report_log"

exit $exit_code
