#!/usr/bin/env bash
# Redline scanner for QuantMind (P0-1 / P0-10 / P1-5 / P1-6 / P1-7).
#
# Exits non-zero on any violation so it can gate CI / pre-commit hooks.
# Runs from the repo root regardless of where it was invoked from.
#
# Add new checks here as new red lines are locked. Each check should:
#   - emit a one-line summary on success
#   - emit grep output on failure, then increment FAIL
# Keep checks pinned to the redline they enforce so removals are auditable.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FAIL=0

# ----------------------------------------------------------------------
# CLI helpers
# ----------------------------------------------------------------------

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

check() {
  # check <description> <expected-empty: 1|0> <grep-args...>
  local desc="$1"; shift
  local expect_empty="$1"; shift
  local out
  out="$(grep -rnE "$@" 2>/dev/null || true)"
  if [ -z "$out" ] && [ "$expect_empty" -eq 1 ]; then
    green "  ok    $desc"
  elif [ -n "$out" ] && [ "$expect_empty" -eq 0 ]; then
    green "  ok    $desc"
  else
    red "  FAIL  $desc"
    printf '%s\n' "$out" | sed 's/^/        /'
    FAIL=$((FAIL + 1))
  fi
}

echo "QuantMind redline check (root: $ROOT)"

# ----------------------------------------------------------------------
# P0-1 — legacy authorization removed
# ----------------------------------------------------------------------
echo
yellow "[P0-1] legacy authorization matrix removed"
# Match the legacy symbols as live code, not docstrings/comments that
# explain the removal. backend/services/run_mode.py + backend/main.py
# keep the legacy names in narrative comments only — allow those two
# files explicitly while forbidding the symbols anywhere else.
LEGACY_OUT="$(grep -rnE --include='*.py' \
  'AUTHORIZATION_MODE|QUANTMIND_PHASE|live_confirm|phase7_live' \
  backend/ 2>/dev/null \
  | grep -vE '^backend/services/run_mode\.py:' \
  | grep -vE '^backend/main\.py:[0-9]+:[[:space:]]*#' \
  || true)"
if [ -z "$LEGACY_OUT" ]; then
  green "  ok    AUTHORIZATION_MODE / QUANTMIND_PHASE absent as live code"
else
  red "  FAIL  AUTHORIZATION_MODE / QUANTMIND_PHASE still in live code"
  printf '%s\n' "$LEGACY_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi
check "auth_mode_change / approval_update WS types absent"         1 \
  "auth_mode_change|approval_update" \
  --include='*.ts' --include='*.vue' --include='*.py' \
  frontend/src/ backend/

# ----------------------------------------------------------------------
# P1-5 — write-endpoint guardrails
# ----------------------------------------------------------------------
echo
yellow "[P1-5] backend write-endpoint allowlist (2 only)"
NON_GET="$(grep -rnE '@(router|app)\.(post|put|patch|delete)\(' \
  --include='*.py' backend/ 2>/dev/null || true)"
# Allowed write endpoints (P1-5 §2):
#   POST /api/execution-reports                       (Phase F)
#   POST /api/reconciliation-tickets/{id}/decide      (Phase F)
ALLOWED='/api/execution-reports|/api/reconciliation-tickets/[{][^}]+[}]/decide'
EXTRA="$(printf '%s\n' "$NON_GET" | grep -vE "$ALLOWED" || true)"
if [ -z "$EXTRA" ]; then
  green "  ok    only allowed write endpoints present"
else
  red "  FAIL  unexpected write endpoint(s):"
  printf '%s\n' "$EXTRA" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

echo
yellow "[P1-5] frontend write-button cleanup"
check "ApprovalQueue.vue absent"                                   1 \
  'ApprovalQueue\.vue' \
  --include='*.ts' --include='*.vue' frontend/src/
check "approveOrder / rejectOrder / cancelOrder absent"            1 \
  'approveOrder|rejectOrder|cancelOrder' \
  --include='*.ts' --include='*.vue' frontend/src/

# ----------------------------------------------------------------------
# P1-6 — loopback-only & secrets hygiene
# ----------------------------------------------------------------------
echo
yellow "[P1-6] all-layer 127.0.0.1 only"
check "Vite + nginx not bound to 0.0.0.0"                          1 \
  "host[[:space:]]*[=:][[:space:]]*['\"]?0\\.0\\.0\\.0|listen[[:space:]]+0\\.0\\.0\\.0" \
  --include='vite.config.ts' --include='*.conf' frontend/ deploy/

echo
yellow "[P1-6] .env free of LLM_KEY/FEISHU_* assignments"
# Per P1-6 §1.1: .env 严禁 LLM_KEY/FEISHU_* 前缀 (assignment). Comments
# documenting which env vars must be set in ~/.bashrc are intentional
# and allowed. The check matches assignment-form lines only.
for envfile in .env .env.example; do
  if [ -f "$envfile" ]; then
    out="$(grep -nE '^[[:space:]]*(DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_[A-Z_]+)=' "$envfile" 2>/dev/null || true)"
    if [ -z "$out" ]; then
      green "  ok    $envfile has no credential assignments"
    else
      red "  FAIL  $envfile contains forbidden credential assignment"
      printf '%s\n' "$out" | sed "s|^|        $envfile:|"
      FAIL=$((FAIL + 1))
    fi
  fi
done

# ----------------------------------------------------------------------
# P0-10 — risk isolation: backend/risk/ never imports LLM / agent layers
# ----------------------------------------------------------------------
echo
yellow "[P0-10] backend/risk isolation"
check "backend/risk does not import llm/agents/mirofish/data"      1 \
  'from backend\.(llm|agents|mirofish|data)|import backend\.(llm|agents|mirofish|data)' \
  --include='*.py' backend/risk/

# ----------------------------------------------------------------------
# A-007 — shadow_baseline isolation
# ----------------------------------------------------------------------
echo
yellow "[A-007] fund_manager_shadow_baseline confined to shadow path"
SHADOW_OUT="$(grep -rnE 'fund_manager_shadow_baseline' \
  --include='*.py' backend/ 2>/dev/null \
  | grep -vE '^backend/services/shadow_(runner|compare|recorder)\.py:' \
  | grep -vE '^backend/main\.py:' \
  || true)"
if [ -z "$SHADOW_OUT" ]; then
  green "  ok    fund_manager_shadow_baseline never referenced outside shadow_*.py"
else
  red "  FAIL  fund_manager_shadow_baseline leaked into a decision-path module"
  printf '%s\n' "$SHADOW_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# YAML frequency must stay shadow_only — startup assertion in
# backend/main.py is the runtime gate; this check protects against an
# operator editing the YAML without running the service.
if [ -f config/agent_models.yaml ]; then
  if grep -A2 'fund_manager_shadow_baseline:' config/agent_models.yaml \
      | grep -qE 'frequency: "shadow_only"'; then
    green "  ok    config/agent_models.yaml has frequency: shadow_only"
  else
    # Search more loosely for the frequency line inside the block.
    FREQ="$(awk '
      /fund_manager_shadow_baseline:/ { inblk=1; next }
      inblk && /^  [a-z_]+:/ && !/^  fund_manager_shadow_baseline:/ { exit }
      inblk && /frequency:/ { print; exit }
    ' config/agent_models.yaml | tr -d '[:space:]')"
    if [ "$FREQ" = 'frequency:"shadow_only"' ]; then
      green "  ok    config/agent_models.yaml has frequency: shadow_only"
    else
      red "  FAIL  fund_manager_shadow_baseline.frequency must be \"shadow_only\""
      printf '        found: %s\n' "$FREQ"
      FAIL=$((FAIL + 1))
    fi
  fi
fi

# ----------------------------------------------------------------------
# P0-8 §1.6.2 / B-001 — evidence_id prefix allowlist
# ----------------------------------------------------------------------
# Any literal assigned to a variable / field named *evidence_id* or
# inserted into an *evidence_ids* tuple must start with one of the five
# locked prefixes (NEWS- / MIROFISH- / MARKET- / RISK- / DEBATE-). The
# runtime gate lives in backend.models.evidence.validate_evidence_id;
# this static scan exists so a typo never reaches the validator.
echo
yellow "[P0-8] evidence_id prefix allowlist"
EVIDENCE_BAD="$(grep -rnE \
  "evidence_id[s]?\s*[:=]\s*[(\\[]?\s*['\"][A-Za-z][^'\"]+['\"]" \
  --include='*.py' backend/ 2>/dev/null \
  | grep -vE "['\"](NEWS|MIROFISH|MARKET|RISK|DEBATE)-" \
  || true)"
if [ -z "$EVIDENCE_BAD" ]; then
  green "  ok    evidence_id literals use only the five locked prefixes"
else
  red "  FAIL  evidence_id literal with unknown prefix (allow: NEWS-/MIROFISH-/MARKET-/RISK-/DEBATE-)"
  printf '%s\n' "$EVIDENCE_BAD" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# A-007 — hot-reload disabled in ConfigService + LLMRouter
# ----------------------------------------------------------------------
echo
yellow "[A-007] hot-reload helpers absent"
check "ConfigService.write_yaml removed"                            1 \
  'def write_yaml|def write_llm_config|def _notify_config_change' \
  --include='config_service.py' backend/services/
check "LLMRouter._maybe_reload_config removed"                      1 \
  'def _maybe_reload_config' \
  --include='router.py' backend/llm/

echo
if [ "$FAIL" -eq 0 ]; then
  green "All redline checks passed."
  exit 0
fi
red "$FAIL redline check(s) failed."
exit 1
