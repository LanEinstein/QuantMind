#!/usr/bin/env bash
#
# QuantMind MongoDB backup (evaluation-period).
#
# Runs mongodump against the docker compose `mongodb` service, stores a
# timestamped archive under BACKUP_DIR (default
# ~/.local/state/quantmind/backups), trims anything older than
# RETENTION_DAYS (default 14), and optionally rsyncs the archive to
# BACKUP_REMOTE.
#
# Exit codes:
#   0  success
#   1  dump failed
#   2  rsync failed (archive kept locally)
#
# Usage (cron-friendly):
#   0 23 * * *  /home/ps/papers/QuantMind/scripts/backup.sh >> logs/backup.log 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Default outside the repo so a stray `git add backups/` cannot leak the
# evaluation-period analysis records or signals into a public commit.
# Override with BACKUP_DIR if a different path is preferred (e.g.
# /var/backups/quantmind on a server).
DEFAULT_BACKUP_DIR="${HOME}/.local/state/quantmind/backups"
BACKUP_DIR="${BACKUP_DIR:-$DEFAULT_BACKUP_DIR}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
DB_NAME="${DB_NAME:-quantmind}"

# Restrictive umask so the archive is mode 0600 (owner-only).
umask 077

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$BACKUP_DIR/mongodump-${DB_NAME}-${ts}.gz"

echo "[$(date -Iseconds)] backup starting -> $archive"

if ! docker compose exec -T mongodb \
    mongodump --archive --gzip --db "$DB_NAME" > "$archive"; then
    echo "[$(date -Iseconds)] mongodump failed" >&2
    rm -f "$archive"
    exit 1
fi

# Defense in depth: re-tighten the file in case umask was overridden by
# the caller's environment.
chmod 600 "$archive"

size=$(stat -c '%s' "$archive" 2>/dev/null || echo 0)
echo "[$(date -Iseconds)] backup size=${size} bytes"

# Trim old archives beyond retention window.
find "$BACKUP_DIR" -name "mongodump-${DB_NAME}-*.gz" \
    -type f -mtime +"$RETENTION_DAYS" -print -delete \
    | sed 's/^/  pruned: /'

# Optional remote sync (rsync destination string such as user@host:/path).
if [[ -n "$BACKUP_REMOTE" ]]; then
    echo "[$(date -Iseconds)] rsync -> $BACKUP_REMOTE"
    if ! rsync -a --partial "$archive" "$BACKUP_REMOTE/"; then
        echo "[$(date -Iseconds)] rsync failed" >&2
        exit 2
    fi
fi

echo "[$(date -Iseconds)] backup done"
