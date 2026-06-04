#!/usr/bin/env bash
# J-003 — Idempotent installer for the quantmind systemd unit.
#
# Steps (all idempotent — re-running is safe):
#   1. Create the dedicated ``quantmind`` system user + group if absent.
#   2. Lay down the systemd unit at /etc/systemd/system/quantmind.service.
#   3. Ensure /home/ps/.quantmind.env exists with chmod 600.
#   4. systemctl daemon-reload + (optionally) enable the unit.
#
# Usage:
#   sudo bash scripts/install_quantmind_service.sh            # install only
#   sudo bash scripts/install_quantmind_service.sh --enable   # install + enable
#   bash scripts/install_quantmind_service.sh --dry-run       # no root + no-op
#
# Output channels (stdout):
#   * "ok: <step>"   — step completed (or already in correct state)
#   * "would: <step>" — dry-run only; what the script would do
#   * "error: <step>" — fatal; script exits non-zero
#
# Exit codes:
#   0  — install completed (or dry-run completed)
#   1  — fatal step failure (missing source files, root required, etc.)
#   2  — invalid CLI arguments
#
# Red lines:
#   * Never overwrites an existing /home/ps/.quantmind.env (would clobber
#     owner-provisioned secrets). The script creates the file ONLY when
#     absent + chmod 600s it.
#   * Refuses to run without root unless --dry-run is passed (the unit
#     install + chown/chmod ops require root).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_SOURCE="${REPO_ROOT}/deploy/quantmind.service"
UNIT_TARGET="/etc/systemd/system/quantmind.service"
ENV_FILE_TARGET="/home/ps/.quantmind.env"
ENV_FILE_TEMPLATE="${REPO_ROOT}/deploy/quantmind.env.example"
SERVICE_USER="quantmind"
SERVICE_GROUP="quantmind"

DRY_RUN=0
ENABLE_UNIT=0

usage() {
    cat <<'EOF'
Usage: install_quantmind_service.sh [--dry-run] [--enable] [--help]

  --dry-run   Show actions without applying them (no root required).
  --enable    After install, systemctl enable the unit (does NOT start).
  --help      Print this message.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --enable)
            ENABLE_UNIT=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument $1" >&2
            usage
            exit 2
            ;;
    esac
done

# Helper to either run or simulate a command depending on $DRY_RUN.
run_or_simulate() {
    local label="$1"
    shift
    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "would: ${label} (cmd: $*)"
    else
        if "$@"; then
            echo "ok: ${label}"
        else
            echo "error: ${label}" >&2
            return 1
        fi
    fi
}

# 0. Sanity — unit source present.
if [[ ! -f "${UNIT_SOURCE}" ]]; then
    echo "error: unit source not found at ${UNIT_SOURCE}" >&2
    exit 1
fi
if [[ ! -f "${ENV_FILE_TEMPLATE}" ]]; then
    echo "error: env template not found at ${ENV_FILE_TEMPLATE}" >&2
    exit 1
fi

# Root required unless dry-run.
if [[ "${DRY_RUN}" != "1" ]] && [[ "$(id -u)" -ne 0 ]]; then
    echo "error: install requires root (rerun with sudo or pass --dry-run)" >&2
    exit 1
fi

# 1. Service user / group.
if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "ok: service user '${SERVICE_USER}' already exists"
else
    run_or_simulate "create system user ${SERVICE_USER}" \
        useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# 1b. Grant the service user traversal + read access to the repo tree
# and rwx on the writable working dirs (Codex cycle 1 P1 fix — the
# unit runs as 'quantmind' but /home/ps has 700 by default; without
# these ACLs systemd starts the unit and uvicorn ImportErrors on the
# unreadable backend tree). Requires the filesystem to support POSIX
# ACLs (ext4 / xfs default — true on every QuantMind host).
if [[ "${DRY_RUN}" == "1" ]]; then
    echo "would: grant ${SERVICE_USER} ACL traversal + read on ${REPO_ROOT}"
    echo "would: grant ${SERVICE_USER} ACL rwx on ${REPO_ROOT}/logs + data"
elif command -v setfacl >/dev/null 2>&1; then
    # Traversal grants on parent path components. Codex cycle 2 P2 fix
    # — these are CRITICAL grants for User=quantmind to reach the repo;
    # do NOT suppress failures (previous ``|| true`` masked permission
    # errors on locked-down hosts and made the unit appear installed
    # while uvicorn would silently fail to import the backend).
    if ! setfacl -m "u:${SERVICE_USER}:x" /home/ps; then
        echo "error: failed to grant ${SERVICE_USER} traversal on /home/ps" >&2
        exit 1
    fi
    if ! setfacl -m "u:${SERVICE_USER}:x" /home/ps/papers; then
        echo "error: failed to grant ${SERVICE_USER} traversal on /home/ps/papers" >&2
        exit 1
    fi
    # Read on the project tree (recursive + default ACL so new files
    # inherit the grant).
    setfacl -R -m "u:${SERVICE_USER}:rX" "${REPO_ROOT}"
    setfacl -R -d -m "u:${SERVICE_USER}:rX" "${REPO_ROOT}"
    # Read+write on the directories the backend actually mutates.
    mkdir -p "${REPO_ROOT}/logs" "${REPO_ROOT}/data"
    setfacl -R -m "u:${SERVICE_USER}:rwX" "${REPO_ROOT}/logs"
    setfacl -R -d -m "u:${SERVICE_USER}:rwX" "${REPO_ROOT}/logs"
    setfacl -R -m "u:${SERVICE_USER}:rwX" "${REPO_ROOT}/data"
    setfacl -R -d -m "u:${SERVICE_USER}:rwX" "${REPO_ROOT}/data"
    # Post-install validation — actually attempt a traverse+read as the
    # service user; fail the install if the grants did not stick.
    # Codex cycle 3 P3 fix — prefer ``runuser`` (root-only, no sudo
    # dependency) when available; fall back to ``sudo``. On minimal
    # systemd hosts ``sudo`` may not be installed; in that case the
    # earlier validation step would falsely fail.
    if command -v runuser >/dev/null 2>&1; then
        _validate_as_service_user() { runuser -u "${SERVICE_USER}" -- "$@"; }
    elif command -v sudo >/dev/null 2>&1; then
        _validate_as_service_user() { sudo -u "${SERVICE_USER}" "$@"; }
    else
        echo "error: neither 'runuser' nor 'sudo' is available to validate" \
             "ACL grants as ${SERVICE_USER}" >&2
        exit 1
    fi
    if ! _validate_as_service_user test -r "${REPO_ROOT}/backend/main.py"; then
        echo "error: ${SERVICE_USER} still cannot read ${REPO_ROOT}/backend/main.py" \
             "— check parent-directory permissions / filesystem ACL support" >&2
        exit 1
    fi
    echo "ok: ACL grants applied + verified for ${SERVICE_USER} on ${REPO_ROOT}"
else
    echo "error: setfacl unavailable — install 'acl' package or grant"     \
        " ${SERVICE_USER} traversal/read on ${REPO_ROOT} manually" >&2
    exit 1
fi

# 2. Unit file install.
if [[ "${DRY_RUN}" == "1" ]]; then
    echo "would: install unit ${UNIT_SOURCE} -> ${UNIT_TARGET}"
else
    install -m 0644 -o root -g root "${UNIT_SOURCE}" "${UNIT_TARGET}"
    echo "ok: unit installed at ${UNIT_TARGET}"
fi

# 3. Env file (DO NOT overwrite if present — would clobber secrets).
if [[ -f "${ENV_FILE_TARGET}" ]]; then
    echo "ok: env file ${ENV_FILE_TARGET} already exists (left untouched)"
else
    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "would: copy ${ENV_FILE_TEMPLATE} -> ${ENV_FILE_TARGET} (chmod 600)"
    else
        install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
            "${ENV_FILE_TEMPLATE}" "${ENV_FILE_TARGET}"
        echo "ok: env file installed at ${ENV_FILE_TARGET} (chmod 600); " \
             "edit + add LLM/Feishu secrets per the template before enabling"
    fi
fi

# 4. systemd reload + optional enable.
run_or_simulate "systemctl daemon-reload" systemctl daemon-reload

if [[ "${ENABLE_UNIT}" == "1" ]]; then
    run_or_simulate "systemctl enable quantmind" systemctl enable quantmind.service
fi

echo
echo "install complete. Next steps:"
echo "  - Edit ${ENV_FILE_TARGET} and add LLM + Feishu secrets."
echo "  - Start the unit:  systemctl start quantmind"
echo "  - Tail journal:    journalctl -u quantmind -f"
echo "  - Smoke check:     curl http://127.0.0.1:8001/api/system/status"
