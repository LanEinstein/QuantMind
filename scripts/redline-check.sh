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
# NOTE: G-009 message-type allowlist is enforced by a dedicated
# AST-aware sub-check further below; the strict ``grep -v`` filter
# excludes the two SSoT declaration sites that have to mention the
# forbidden strings to keep the allowlist authoritative.

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

echo
yellow "[P1-6 / H-001] secrets_validator + gitleaks artefacts present"
# Three artefacts ship together — missing any one weakens the gate:
#   1. backend/services/secrets_validator.py (runtime fail-fast)
#   2. .pre-commit-config.yaml referencing gitleaks v8.18+ (pre-commit gate)
#   3. .gitleaks.toml with rules for the locked 8-credential pool
SECRETS_OUT="$(python3 - <<'PY'
import pathlib
import re
import sys

findings: list[str] = []

validator = pathlib.Path("backend/services/secrets_validator.py")
if not validator.exists():
    findings.append("backend/services/secrets_validator.py missing")
else:
    text = validator.read_text(encoding="utf-8")
    # Pool composition lock — 5 Feishu credentials, 3 LLM keys, custom-bot retired.
    expected = (
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_VERIFY_TOKEN",
        "FEISHU_ENCRYPT_KEY",
        "FEISHU_ALERT_CHAT_ID",
    )
    for name in expected:
        if name not in text:
            findings.append(f"secrets_validator.py missing {name}")
    legacy = (
        "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
        "FEISHU_CUSTOM_BOT_SIGN_SECRET",
    )
    for name in legacy:
        if name not in text:
            findings.append(
                f"secrets_validator.py must reference legacy {name} "
                "for soft-warning matrix"
            )

pre_commit = pathlib.Path(".pre-commit-config.yaml")
if not pre_commit.exists():
    findings.append(".pre-commit-config.yaml missing")
else:
    pc_text = pre_commit.read_text(encoding="utf-8")
    if "gitleaks" not in pc_text:
        findings.append(".pre-commit-config.yaml has no gitleaks hook")
    # Require v8.18 or higher.
    match = re.search(r"rev:\s*v(\d+)\.(\d+)", pc_text)
    if match is None:
        findings.append(".pre-commit-config.yaml gitleaks rev not pinned")
    else:
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) < (8, 18):
            findings.append(
                f".pre-commit-config.yaml gitleaks rev v{major}.{minor} "
                "must be >= v8.18"
            )

gitleaks = pathlib.Path(".gitleaks.toml")
if not gitleaks.exists():
    findings.append(".gitleaks.toml missing")
else:
    gl_text = gitleaks.read_text(encoding="utf-8")
    required_rule_ids = (
        "deepseek-api-key",
        "dashscope-api-key",
        "moonshot-api-key",
        "feishu-app-id",
        "feishu-app-secret",
        "feishu-verify-token",
        "feishu-encrypt-key",
        "feishu-alert-chat-id",
    )
    for rid in required_rule_ids:
        if rid not in gl_text:
            findings.append(f".gitleaks.toml missing rule id {rid!r}")
    # Custom-bot rules MUST NOT ship — they would lock in dead schemas.
    if "feishu-custom-bot" in gl_text.lower():
        findings.append(
            ".gitleaks.toml ships a feishu-custom-bot rule "
            "(removed by P0-2-amendment-2026-05-16)"
        )

runbook = pathlib.Path("docs/runbook/secrets-incident-response.md")
if not runbook.exists():
    findings.append("docs/runbook/secrets-incident-response.md missing")

if findings:
    print("\n".join(findings))
    sys.exit(1)
PY
)"
SECRETS_RC=$?
if [ "$SECRETS_RC" -eq 0 ]; then
  green "  ok    secrets_validator + gitleaks + runbook all present and locked"
else
  red "  FAIL  secrets_validator artefacts are incomplete:"
  printf '%s\n' "$SECRETS_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# P0-10 — risk isolation: backend/risk/ never imports LLM / agent layers
# ----------------------------------------------------------------------
echo
yellow "[P0-10] backend/risk isolation"
check "backend/risk does not import llm/agents/mirofish/data"      1 \
  'from backend\.(llm|agents|mirofish|data)|import backend\.(llm|agents|mirofish|data)' \
  --include='*.py' backend/risk/

# ----------------------------------------------------------------------
# P0-8 §2 redline 8 / P1-2.B §2 redline 8 — data-quality boundary
# (data_quality.py / staleness.py / divergence.py / suspension.py never
# import backend.llm / backend.agents / backend.risk / backend.mirofish).
# MiroFish health hook is wired through a Protocol so no concrete
# import is required (P1-2.B §2 redline 8).
#
# Uses Python's ``ast`` module so the guard catches every import form
# — dotted, package-level, relative, and multiline ``from backend
# import (\n llm,\n)`` shapes that line-oriented grep would miss.
# ----------------------------------------------------------------------
echo
yellow "[P0-8] data-quality boundary"
BOUNDARY_OUT="$(python3 - <<'PY'
import ast, sys
from pathlib import Path

FILES = [
    "backend/data/data_quality.py",
    "backend/data/staleness.py",
    "backend/data/divergence.py",
    "backend/data/suspension.py",
]
FORBIDDEN = {"llm", "agents", "risk", "mirofish"}

bad: list[str] = []
for path in FILES:
    p = Path(path)
    if not p.exists():
        continue
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError as exc:
        bad.append(f"{path}: parse error: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".") if mod else []
            # from backend.llm[.x] import ...
            if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in FORBIDDEN:
                bad.append(f"{path}:{node.lineno}: from {mod} import ...")
            # from backend import llm
            if mod == "backend":
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        bad.append(
                            f"{path}:{node.lineno}: from backend import {alias.name}"
                        )
            # from ..llm import X  (relative)
            if node.level and parts and parts[0] in FORBIDDEN:
                bad.append(
                    f"{path}:{node.lineno}: from {'.' * node.level}{mod} import ..."
                )
            # from .. import llm  (relative)
            if node.level and not mod:
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        bad.append(
                            f"{path}:{node.lineno}: from {'.' * node.level} import {alias.name}"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in FORBIDDEN:
                    bad.append(f"{path}:{node.lineno}: import {alias.name}")

if bad:
    for line in bad:
        print(line)
    sys.exit(1)
PY
)"
BOUNDARY_RC=$?
if [ $BOUNDARY_RC -eq 0 ]; then
  green "  ok    data_quality / staleness / divergence / suspension isolated"
else
  red "  FAIL  data-quality boundary imports llm/agents/risk/mirofish"
  printf '%s\n' "$BOUNDARY_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

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
  | grep -vE "['\"](NEWS|MIROFISH|MARKET|RISK|DEBATE|THEME)-" \
  || true)"
if [ -z "$EVIDENCE_BAD" ]; then
  green "  ok    evidence_id literals use only the six locked prefixes"
else
  red "  FAIL  evidence_id literal with unknown prefix (allow: NEWS-/MIROFISH-/MARKET-/RISK-/DEBATE-/THEME-)"
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

# ----------------------------------------------------------------------
# P0-7 — RiskConfig immutability & boundary
# ----------------------------------------------------------------------
echo
yellow "[P0-7] RiskConfig immutability"
# 1. backend/api/risk/* must remain GET-only (already covered by the
#    global P1-5 write-endpoint allowlist above, but lock the risk
#    surface explicitly so a future allowlist relaxation never
#    re-opens it).
RISK_WRITE="$(grep -nE '@(router|app)\.(post|put|patch|delete)\(' \
  backend/api/risk.py backend/api/risk_*.py 2>/dev/null || true)"
# backend/api/risk.py is a single file (not a package directory); the
# additional ``risk_*.py`` glob captures any future sibling risk modules
# (e.g. ``risk_proposals.py``) without re-introducing a directory-shaped
# false negative. Codex cycle 1 P3.
if [ -z "$RISK_WRITE" ]; then
  green "  ok    backend/api/risk*.py has no write endpoints"
else
  red "  FAIL  backend/api/risk*.py has write endpoint(s):"
  printf '%s\n' "$RISK_WRITE" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# 2. RiskConfig submodels must stay frozen=True (lock down the
#    immutability invariant that powers redline 1 / redline 16).
RISK_FROZEN_OUT="$(python3 - <<'PY'
import ast
import pathlib
import sys

path = pathlib.Path("backend/broker/models.py")
tree = ast.parse(path.read_text(encoding="utf-8"))

required = {
    "RiskConfig",
    "PositionLimitsConfig",
    "CircuitBreakerConfig",
    "UniverseConfig",
    "StopLossConfig",
}
findings: list[str] = []

for node in ast.walk(tree):
    if not isinstance(node, ast.ClassDef):
        continue
    if node.name not in required:
        continue
    frozen_seen = False
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        if "model_config" not in targets:
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        for kw in stmt.value.keywords:
            if (
                kw.arg == "frozen"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                frozen_seen = True
                break
    if not frozen_seen:
        findings.append(f"{node.name} missing frozen=True")

if findings:
    print("\n".join(findings))
    sys.exit(1)
PY
)"
RISK_FROZEN_EXIT=$?
if [ "$RISK_FROZEN_EXIT" -eq 0 ]; then
  green "  ok    Risk submodels declare ConfigDict(frozen=True)"
else
  red "  FAIL  Risk submodels missing frozen=True:"
  printf '%s\n' "$RISK_FROZEN_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# 3. backend/agents + backend/llm + backend/mirofish must not import
#    RiskConfig / submodels (P0-7 §2 redline 1 / redline 11). Use Python's
#    ``ast`` so the guard catches every import shape — dotted, parenthesised
#    multiline, relative — that line-oriented grep would miss
#    (codex cycle 3 P2).
RISK_IMPORT_OUT="$(python3 - <<'PY'
import ast
import pathlib
import sys

FORBIDDEN_NAMES = {
    "RiskConfig",
    "PositionLimitsConfig",
    "StopLossConfig",
    "CircuitBreakerConfig",
    "UniverseConfig",
}
# Any of these module roots — including submodules like
# ``backend.risk.engine`` — may not re-export RiskConfig sub-types into
# LLM/agent/mirofish code. The check folds equivalent shapes (``from M
# import X`` / ``from M.sub import X`` / ``import M``) so a forbidden
# leak via re-export cannot slip through unnoticed.
RESTRICTED_PREFIXES = ("backend.broker.models", "backend.risk")
SEARCH_ROOTS = ("backend/agents", "backend/llm", "backend/mirofish")


def _is_restricted_module(mod: str) -> bool:
    return any(
        mod == p or mod.startswith(p + ".")
        for p in RESTRICTED_PREFIXES
    )


findings: list[str] = []
seen: set[tuple[str, int, str]] = set()


def _record(path: pathlib.Path, line: int, msg: str) -> None:
    key = (str(path), line, msg)
    if key in seen:
        return
    seen.add(key)
    findings.append(f"{path}:{line}: {msg}")


for root in SEARCH_ROOTS:
    base = pathlib.Path(root)
    if not base.exists():
        continue
    for path in base.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if not _is_restricted_module(mod):
                    continue
                for alias in node.names:
                    if alias.name in FORBIDDEN_NAMES:
                        _record(
                            path, node.lineno,
                            f"from {mod} import {alias.name}",
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_restricted_module(alias.name):
                        _record(
                            path, node.lineno,
                            f"import {alias.name}",
                        )

if findings:
    print("\n".join(findings))
    sys.exit(1)
PY
)"
RISK_IMPORT_EXIT=$?
if [ "$RISK_IMPORT_EXIT" -eq 0 ]; then
  green "  ok    backend/agents+llm+mirofish do not import RiskConfig"
else
  red "  FAIL  RiskConfig leaked into LLM/agent/mirofish layer:"
  printf '%s\n' "$RISK_IMPORT_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# 4. config/risk.yaml must declare the P0-7 conservative trio + halt
#    quartet + universe whitelist as locked values. Any divergence
#    requires a P0-7 amendment + this guard update.
RISK_YAML_OUT="$(python3 - <<'PY'
import pathlib
import sys

import yaml

path = pathlib.Path("config/risk.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8"))

required = {
    ("position_limits", "max_single_stock_pct"): 0.15,
    ("position_limits", "max_total_position_pct"): 0.70,
    ("position_limits", "max_single_instruction_amount"): 50000,
    ("position_limits", "max_daily_new_instructions"): 5,
    ("circuit_breaker", "daily_loss_limit_pct"): 0.05,
    ("circuit_breaker", "consecutive_loss_count"): 3,
    ("circuit_breaker", "cooldown_minutes"): 60,
    ("circuit_breaker", "apply_to_sell_orders"): False,
    ("universe", "forbidden_st"): True,
    ("universe", "forbid_buy_at_limit_up"): True,
    ("universe", "forbid_sell_at_limit_down"): True,
}
findings: list[str] = []
for (section, key), expected in required.items():
    actual = data.get(section, {}).get(key)
    if actual != expected:
        findings.append(f"{section}.{key} = {actual!r} (expected {expected!r})")

allowed_boards = data.get("universe", {}).get("allowed_boards")
if list(allowed_boards or ()) != ["sh_main", "sz_main", "chuangye", "etf"]:
    findings.append(
        f"universe.allowed_boards = {allowed_boards!r} "
        '(expected ["sh_main","sz_main","chuangye","etf"])'
    )

# Lock the per-board limit-up/down pct table — a silent edit (e.g.
# ``sh_main: 0.99``) would defeat check 2/12 without triggering the
# rest of the redline scan. Codex cycle 2 P2.
expected_price_limit = {
    "sh_main": 0.10, "sz_main": 0.10,
    "chuangye": 0.20, "etf": 0.10,
}
actual_price_limit = (data.get("universe") or {}).get("price_limit_pct_by_board") or {}
if dict(actual_price_limit) != expected_price_limit:
    findings.append(
        f"universe.price_limit_pct_by_board = {dict(actual_price_limit)!r} "
        f"(expected {expected_price_limit!r})"
    )

if findings:
    print("\n".join(findings))
    sys.exit(1)
PY
)"
RISK_YAML_EXIT=$?
if [ "$RISK_YAML_EXIT" -eq 0 ]; then
  green "  ok    config/risk.yaml matches P0-7 locked values"
else
  red "  FAIL  config/risk.yaml diverges from P0-7 lock:"
  printf '%s\n' "$RISK_YAML_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# G-009 — WebSocket forbidden-message-kind grep (P1-5 §2 红线 4)
# auth_mode_change / approval_update were removed by G-009; if either
# reappears in backend or frontend the WS bridge could re-introduce the
# Phase A authorization matrix or the destructively-removed ApprovalQueue.
# ----------------------------------------------------------------------
echo
yellow "[G-009] WS forbidden message kinds removed"
# The two SSoT locations that declare the forbidden vocabulary (so the
# downstream allowlist stays grep-able) are intentional and excluded:
#   - backend/data/publisher.py FORBIDDEN_WS_TYPES literal
#   - frontend/src/types/market.ts FORBIDDEN_WS_MESSAGE_TYPES literal
# Anywhere else (production code, route handlers, stores) referencing
# the forbidden kinds is a violation.
G009_BACKEND="$(grep -rnE 'auth_mode_change|approval_update' \
  --include='*.py' backend/ 2>/dev/null \
  | grep -vE '^backend/data/publisher\.py:' \
  || true)"
G009_FRONTEND="$(grep -rnE 'auth_mode_change|approval_update' \
  --include='*.ts' --include='*.vue' frontend/src/ 2>/dev/null \
  | grep -vE '^frontend/src/types/market\.ts:' \
  | grep -vE '/__tests__/' \
  || true)"
if [ -z "$G009_BACKEND" ] && [ -z "$G009_FRONTEND" ]; then
  green "  ok    auth_mode_change / approval_update absent from live code"
else
  red "  FAIL  forbidden WS message kind(s) leaked:"
  [ -n "$G009_BACKEND" ] && printf '%s\n' "$G009_BACKEND" | sed 's/^/        backend: /'
  [ -n "$G009_FRONTEND" ] && printf '%s\n' "$G009_FRONTEND" | sed 's/^/        frontend: /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# H-003 / P1-7 — cost_guard + soft_degrade_manager isolation
# (CLAUDE.md §2.10): the budget guard MUST NOT import backend.{llm,
# agents,mirofish,data}. Spend data flows through backend.services.
# cost_probe which is Redis-only.
# ----------------------------------------------------------------------
echo
yellow "[H-003] cost_guard + SoftDegradeManager isolation"
COST_GUARD_OUT="$(python3 - <<'PY'
import ast
import pathlib
import sys

FILES = [
    "backend/services/cost_guard.py",
    "backend/services/soft_degrade_manager.py",
    "backend/services/cost_probe.py",
]
FORBIDDEN = {"llm", "agents", "mirofish", "data"}
findings: list[str] = []

for path in FILES:
    p = pathlib.Path(path)
    if not p.exists():
        findings.append(f"{path}: missing")
        continue
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        findings.append(f"{path}: parse error: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".") if mod else []
            if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in FORBIDDEN:
                findings.append(f"{path}:{node.lineno}: from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in FORBIDDEN:
                    findings.append(f"{path}:{node.lineno}: import {alias.name}")

if findings:
    print("\n".join(findings))
    sys.exit(1)
PY
)"
COST_GUARD_RC=$?
if [ $COST_GUARD_RC -eq 0 ]; then
  green "  ok    cost_guard + soft_degrade_manager + cost_probe pure"
else
  red "  FAIL  cost_guard pipeline imports llm/agents/mirofish/data:"
  printf '%s\n' "$COST_GUARD_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# cost API must remain GET-only (P1-5 §2 红线 1).
COST_WRITE="$(grep -nE '@(router|app)\.(post|put|patch|delete)\(' \
  backend/api/cost.py 2>/dev/null || true)"
if [ -z "$COST_WRITE" ]; then
  green "  ok    backend/api/cost.py has no write endpoints"
else
  red "  FAIL  backend/api/cost.py has write endpoint(s):"
  printf '%s\n' "$COST_WRITE" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# X-018 / P2-2 §2 red line 17 — Phase X import isolation
# Every Phase X module (backend/evolution/** + the 8 Phase X service
# files) must NOT import backend.{api,broker,risk,llm,agents,mirofish,
# data}. The same boundary is enforced by:
#   * pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api]
#   * tests/test_phase_x_imports.py AST scan
# This grep-cum-AST sub-check is the pre-commit / CI gate.
# ----------------------------------------------------------------------
echo
yellow "[X-018] Phase X import isolation (P2-2 §2 red line 17)"
PHASE_X_OUT="$(python3 - <<'PY'
import ast
import pathlib
import sys

FORBIDDEN = {"api", "broker", "risk", "llm", "agents", "mirofish", "data"}
PHASE_X_SERVICE_FILES = (
    "backend/services/prompt_registry.py",
    "backend/services/shadow_chain.py",
    "backend/services/exemplar_selector.py",
    "backend/services/dspy_gepa_runner.py",
    "backend/services/evolution_dispatcher.py",
    "backend/services/amendment_drafter.py",
    "backend/services/evolution_feishu_notifier.py",
    "backend/services/evolution_audit_writer.py",
)


def _is_forbidden(mod: str) -> bool:
    parts = mod.split(".") if mod else []
    return (
        len(parts) >= 2
        and parts[0] == "backend"
        and parts[1] in FORBIDDEN
    )


def _iter_paths():
    root = pathlib.Path("backend/evolution")
    if root.exists():
        yield from sorted(root.rglob("*.py"))
    for name in PHASE_X_SERVICE_FILES:
        path = pathlib.Path(name)
        if path.exists():
            yield path


violations: list[str] = []
for path in _iter_paths():
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            level = node.level or 0
            # Absolute form: ``from backend.api import ...``
            if _is_forbidden(mod):
                violations.append(
                    f"{path}:{node.lineno}: from {mod} import ..."
                )
            elif mod == "backend":
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        violations.append(
                            f"{path}:{node.lineno}: "
                            f"from backend import {alias.name}"
                        )
            # Package-relative ``from ..api import router`` —
            # ``mod`` is just ``api`` and ``level`` is non-zero, so
            # the ``backend.``-prefix check above misses it (codex
            # review P2 cycle 1).
            if level > 0 and mod:
                parts = mod.split(".")
                if parts and parts[0] in FORBIDDEN:
                    violations.append(
                        f"{path}:{node.lineno}: "
                        f"from {'.' * level}{mod} import ..."
                    )
            # Relative ``from .. import api`` — empty module + level>0.
            if level > 0 and not mod:
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        violations.append(
                            f"{path}:{node.lineno}: "
                            f"from {'.' * level} import {alias.name}"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    violations.append(
                        f"{path}:{node.lineno}: import {alias.name}"
                    )

if violations:
    for line in violations:
        print(line)
    sys.exit(1)
PY
)"
PHASE_X_RC=$?
if [ $PHASE_X_RC -eq 0 ]; then
  green "  ok    Phase X modules do not import backend.{api,broker,risk,llm,agents,mirofish,data}"
else
  red "  FAIL  Phase X module(s) import a decision-path subpackage:"
  printf '%s\n' "$PHASE_X_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# K-006 / R0 §3 new red line A — PIT data reproducibility
# Module 0 backend/marketdata_snapshot/ must:
#   1. NOT import backend.{llm,agents,mirofish} (LLM<->data isolation;
#      it stays a pure storage/replay layer — the orchestration layer
#      passes payloads in).
#   2. Store RAW BYTES, not hash-only: MarketDataSnapshot must declare a
#      ``raw_payload: bytes`` field (red line A.1, Codex showstopper #1).
# Paired with tests/marketdata_snapshot/test_module_contract.py (AST).
# ----------------------------------------------------------------------
yellow "[K-006] PIT snapshot module isolation + raw-bytes (R0 §3 red line A)"
PIT_OUT="$(python3 - <<'PY'
import ast
import pathlib
import sys

FORBIDDEN = {"llm", "agents", "mirofish"}
ROOT = pathlib.Path("backend/marketdata_snapshot")
violations: list[str] = []

# 1. import isolation
for path in sorted(ROOT.rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        continue
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
    for mod in mods:
        parts = mod.split(".")
        if len(parts) >= 2 and parts[0] == "backend" and parts[1] in FORBIDDEN:
            violations.append(f"{path}: forbidden import {mod}")

# 2. raw bytes, not hash-only
snap = ROOT / "snapshot.py"
raw_bytes_field = False
if snap.exists():
    tree = ast.parse(snap.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "MarketDataSnapshot"
        ):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "raw_payload"
                    and isinstance(stmt.annotation, ast.Name)
                    and stmt.annotation.id == "bytes"
                ):
                    raw_bytes_field = True
else:
    violations.append("backend/marketdata_snapshot/snapshot.py missing")
if not raw_bytes_field:
    violations.append(
        "MarketDataSnapshot lacks a ``raw_payload: bytes`` field "
        "(hash-only variant forbidden — R0 §3 red line A.1)"
    )

if violations:
    print("\n".join(violations))
    sys.exit(1)
PY
)"
PIT_RC=$?
if [ $PIT_RC -eq 0 ]; then
  green "  ok    marketdata_snapshot pure + MarketDataSnapshot stores raw bytes"
else
  red "  FAIL  PIT snapshot module red line A violated:"
  printf '%s\n' "$PIT_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# L-001 / P0-9-amendment-2026-05-24 — full-market universe ruleset.
# The fixed 13-code lock must be gone from config/, replaced by a board
# whitelist + forbidden-board set. universe_policy.{yaml,py} replaces the
# renamed watchlist_policy.{yaml,py}; the dead invariants
# (total_codes=13 / watchlist_size_must_equal / required_etfs) must not
# reappear in config/ (CLAUDE.md §5 grep ∅).
# ----------------------------------------------------------------------
yellow "[L-001] universe ruleset replaces 13-code lock (P0-9-amendment-2026-05-24)"
L001_FAIL=0
if [ ! -f config/universe_policy.yaml ]; then
  red "  FAIL  config/universe_policy.yaml missing"
  L001_FAIL=1
fi
if [ -f config/watchlist_policy.yaml ]; then
  red "  FAIL  config/watchlist_policy.yaml still present (renamed to universe_policy.yaml)"
  L001_FAIL=1
fi
if [ -f backend/services/watchlist_policy.py ]; then
  red "  FAIL  backend/services/watchlist_policy.py still present (renamed to universe_policy.py)"
  L001_FAIL=1
fi
L001_DEAD="$(grep -rnE 'total_codes.*13|watchlist_size_must_equal|required_etfs' config/ 2>/dev/null || true)"
if [ -n "$L001_DEAD" ]; then
  red "  FAIL  dead 13-code invariants still in config/:"
  printf '%s\n' "$L001_DEAD" | sed 's/^/        /'
  L001_FAIL=1
fi
if ! grep -q 'board_whitelist:' config/universe_policy.yaml 2>/dev/null \
   || ! grep -q 'forbidden_boards:' config/universe_policy.yaml 2>/dev/null; then
  red "  FAIL  universe_policy.yaml missing board_whitelist / forbidden_boards"
  L001_FAIL=1
fi
if [ $L001_FAIL -eq 0 ]; then
  green "  ok    universe_policy ruleset present + no 13-code lock in config/"
else
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# L-002 / P0-9-amendment-2026-05-24 §4 red line 8 — screening (+ the other
# pure-quant Line-1 modules) must never import backend.{llm,agents,
# mirofish}. backend.data/risk/marketdata_snapshot ARE allowed (board
# classification, risk types, PIT snapshots) — those are not scanned here.
# ----------------------------------------------------------------------
yellow "[L-002] pure-quant module isolation (no backend.{llm,agents,mirofish})"
L002_DIRS=""
for d in backend/screening backend/budget_policy backend/candidate_selector \
         backend/slot_portfolio; do
  [ -d "$d" ] && L002_DIRS="$L002_DIRS $d"
done
if [ -z "$L002_DIRS" ]; then
  green "  ok    no pure-quant modules present yet (skip)"
else
  # shellcheck disable=SC2086
  L002_OUT="$(grep -rnE 'import +backend\.(llm|agents|mirofish)|from +backend\.(llm|agents|mirofish)' $L002_DIRS 2>/dev/null || true)"
  if [ -n "$L002_OUT" ]; then
    red "  FAIL  pure-quant module imports a forbidden subpackage:"
    printf '%s\n' "$L002_OUT" | sed 's/^/        /'
    FAIL=$((FAIL + 1))
  else
    green "  ok    screening/budget_policy/candidate_selector free of llm/agents/mirofish"
  fi
fi

# ----------------------------------------------------------------------
# L-003 / P0-7-amendment-2026-05-24 — budget_tiers config exists + the 15%
# single-stock pct is NOT duplicated in budget_tiers (it must stay
# single-source in position_limits). The cash thresholds + ETF whitelist
# live in budget_tiers; runtime-immutable like the rest of risk.yaml.
# ----------------------------------------------------------------------
yellow "[L-003] budget_tiers config present + single-source 15% pct"
L003_FAIL=0
if [ -f config/risk.yaml ]; then
  for key in "budget_tiers:" "micro_max_cash_yuan:" "small_max_cash_yuan:" \
             "etf_whitelist:"; do
    if ! grep -q "$key" config/risk.yaml; then
      red "  FAIL  config/risk.yaml missing budget_tiers key: $key"
      L003_FAIL=1
    fi
  done
  # The 15% pct must NOT be re-declared inside the budget_tiers block —
  # budget_policy reads it from position_limits (single source of truth).
  L003_DUP="$(awk '/^budget_tiers:/{f=1;next} /^[a-z]/{f=0} f && /max_single_stock_pct/' config/risk.yaml || true)"
  if [ -n "$L003_DUP" ]; then
    red "  FAIL  max_single_stock_pct duplicated inside budget_tiers (must be single-source in position_limits)"
    L003_FAIL=1
  fi
else
  red "  FAIL  config/risk.yaml missing"
  L003_FAIL=1
fi
if [ $L003_FAIL -eq 0 ]; then
  green "  ok    budget_tiers present; 15% pct single-source in position_limits"
else
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# L-004 / P0-7-amendment-2026-05-24 §2.4 — RiskEngine concentration_exception:
#   * config/risk.yaml has a concentration_exception section;
#   * its etf_whitelist matches budget_tiers.etf_whitelist (consistency —
#     RiskEngine still re-derives at runtime, this only blocks silent drift);
#   * the engine independently re-validates (grep _grant_concentration_exception);
#   * risk_summary stays min=max=14 (方案 A — no new check).
# ----------------------------------------------------------------------
yellow "[L-004] RiskEngine concentration_exception re-validation (方案 A, 14-check)"
L004_FAIL=0
if ! grep -q "concentration_exception:" config/risk.yaml 2>/dev/null; then
  red "  FAIL  config/risk.yaml missing concentration_exception section"
  L004_FAIL=1
fi
if ! grep -q "_grant_concentration_exception" backend/risk/engine.py 2>/dev/null; then
  red "  FAIL  RiskEngine missing independent _grant_concentration_exception re-validation"
  L004_FAIL=1
fi
if ! grep -qE "min_length=14, max_length=14" backend/models/instruction.py 2>/dev/null; then
  red "  FAIL  InstructionPlan.risk_summary is no longer min=max=14 (方案 A broken)"
  L004_FAIL=1
fi
# Whitelist consistency: the two etf_whitelist blocks (budget_tiers +
# concentration_exception) must enumerate the same codes.
L004_WL="$(python3 - <<'PY' 2>/dev/null || true
import yaml
raw = yaml.safe_load(open("config/risk.yaml", encoding="utf-8")) or {}
bt = set(map(str, (raw.get("budget_tiers") or {}).get("etf_whitelist") or []))
ce = set(map(str, (raw.get("concentration_exception") or {}).get("etf_whitelist") or []))
print("MISMATCH" if bt != ce else "OK")
PY
)"
if [ "$L004_WL" != "OK" ]; then
  red "  FAIL  budget_tiers.etf_whitelist != concentration_exception.etf_whitelist (drift)"
  L004_FAIL=1
fi
if [ $L004_FAIL -eq 0 ]; then
  green "  ok    concentration_exception config + re-validation + 14-check intact"
else
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# M-004 / R0 §4 new red line B — InstructionPlan single construction point
#
# Only the model definition (backend/models/instruction.py) and the sole
# builder (backend/services/instruction_plan_builder.py) may name the
# ``InstructionPlan(`` constructor anywhere under backend/. Any other
# backend file constructing it is a provenance breach: side / volume /
# limit_price must be deterministically derived by the builder from
# non-LLM inputs, never assembled elsewhere and never from LLM JSON
# (CLAUDE.md §2.0 red line B). Import isolation alone cannot prove field
# provenance — the single construction point + the adversarial tests in
# tests/test_instructionplan_provenance.py are the real boundary.
# ----------------------------------------------------------------------
echo
yellow "[M-004] InstructionPlan single construction point (R0 §4 red line B)"
# AST scan (not raw grep): resolve InstructionPlan construction calls including
# aliased imports (``import InstructionPlan as Plan; Plan(...)``) and attribute
# calls (``module.InstructionPlan(...)``) so the gate cannot be evaded by an
# alias (codex M-004 P2). A raw-text grep misses both.
M004_OUT="$(python3 - <<'PY' 2>/dev/null || echo "SCANNER_ERROR"
import ast, pathlib

ALLOWED = {
    pathlib.Path("backend/services/instruction_plan_builder.py"),
    pathlib.Path("backend/models/instruction.py"),
}
violations = []
for py in sorted(pathlib.Path("backend").rglob("*.py")):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    # Local names bound to the InstructionPlan class. Seed from ANY
    # ``from ... import InstructionPlan [as X]`` regardless of module so a
    # re-export alias (``from backend.models import InstructionPlan as Plan``)
    # is not missed (codex M-004 verify finding). The repo has exactly one
    # InstructionPlan class, so matching the imported name is safe; a
    # type-hint-only import with no construction Call is never flagged.
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "InstructionPlan":
                    names.add(a.asname or a.name)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            found = True
            break
        if isinstance(f, ast.Attribute) and f.attr == "InstructionPlan":
            found = True
            break
    if found and py not in ALLOWED:
        violations.append(str(py))
print("\n".join(violations))
PY
)"
if [ "$M004_OUT" = "SCANNER_ERROR" ]; then
  red "  FAIL  [M-004] InstructionPlan AST scanner error"
  FAIL=$((FAIL + 1))
elif [ -n "$M004_OUT" ]; then
  red "  FAIL  InstructionPlan constructed outside {models/instruction.py, instruction_plan_builder.py}:"
  printf '%s\n' "$M004_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
else
  green "  ok    InstructionPlan only constructed in model + builder (AST, alias-aware)"
fi

# ----------------------------------------------------------------------
# M-005 / P1-7-amendment-2026-05-24 (+ 2026-05-26) — cost_guard pre-call
# reservation + fan-out cap. The daily hard cap must be enforced as a real
# pre-call reservation (reserve_budget / settle_budget), the debate must be
# gated by the max_debates_per_day fan-out cap (reserve_debate_slot), and the
# 4 P1-7 ceiling constants must keep their locked values. P1-7-amendment
# 2026-05-26 raised ONLY the daily hard cap ¥20 → ¥100 (the sole full-LLM
# circuit breaker); soft 0.7 / monthly ¥440 / Kimi ¥4 are unchanged. The
# reservation key stays in the unified ``llm:usage`` namespace (amendment §2.4).
# ----------------------------------------------------------------------
echo
yellow "[M-005] cost_guard pre-call reservation + fan-out cap (P1-7-amendment 2026-05-24/26)"
M005_FAIL=0
CG="backend/services/cost_guard.py"
for sym in "def reserve_budget" "def settle_budget" "def reserve_debate_slot" \
           "_DEFAULT_MAX_DEBATES_PER_DAY" "_DEFAULT_MAX_ANOMALY_LLM_PER_DAY"; do
  if ! grep -q "$sym" "$CG" 2>/dev/null; then
    red "  FAIL  cost_guard missing: $sym"
    M005_FAIL=1
  fi
done
# The 4 P1-7 ceiling constants must keep their locked values. The 2026-05-24
# amendment changed execution semantics (not the numbers); the 2026-05-26
# amendment raised ONLY the daily hard cap ¥20 → ¥100 (soft 0.7 / monthly ¥440
# / Kimi ¥4 unchanged). Any further drift = unauthorized red-line change.
for kv in "_DEFAULT_DAILY_BUDGET_RMB = 100.0" "_DEFAULT_SOFT_CEIL_PCT = 0.7" \
          "_DEFAULT_MONTHLY_BUDGET_RMB = 440.0" "_DEFAULT_KIMI_DAILY_CAP_RMB = 4.0"; do
  if ! grep -qF "$kv" "$CG" 2>/dev/null; then
    red "  FAIL  cost_guard P1-7 constant changed/missing: $kv"
    M005_FAIL=1
  fi
done
# The reservation counter must live in the unified llm:usage namespace.
if ! grep -qE '_RESERVED_KEY_PREFIX *= *"llm:usage"' "$CG" 2>/dev/null; then
  red "  FAIL  reservation key not in unified llm:usage namespace (amendment §2.4)"
  M005_FAIL=1
fi
# agents_team debate orchestration must go through the reservation API.
if ! grep -q "reserve_debate_slot" backend/agents_team/graph.py 2>/dev/null; then
  red "  FAIL  agents_team/graph.py does not claim a debate slot (fan-out cap bypass)"
  M005_FAIL=1
fi
if [ $M005_FAIL -eq 0 ]; then
  green "  ok    cost_guard reserve/settle + fan-out cap present; 4 ceilings unchanged"
else
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# N-005 / P0-10-amendment-2026-05-25 §2.5 — Line-2 monitoring module
# isolation. backend/monitoring must never import backend.{llm,agents,
# agents_team,mirofish}: the SELL/ADD direction is a deterministic quant
# observation, never an LLM output (the triggered LLM is reached only via
# cost_guard + Redis, orchestrated OUTSIDE this module). agents_team is the
# Line-1 LLM debate path (run_shortlist / fund_manager) — forbidding it stops
# the multi-agent LLM debate leaking back into Line-2 (codex N-005).
# backend.{broker,data,risk,services,integrations,marketdata_snapshot,models}
# ARE allowed and not scanned. The regex covers dotted, name-level
# (``from backend import llm``) and relative (``from ..agents import x``)
# forms; the AST pytest (tests/monitoring/test_module_contract.py) is the
# authoritative guard and this grep is the standalone-CI fast gate.
# ----------------------------------------------------------------------
echo
yellow "[N-005] Line-2 monitoring isolation (no backend.{llm,agents,agents_team,mirofish})"
if [ -d backend/monitoring ]; then
  _N005_NAMES='llm|agents_team|agents|mirofish'
  N005_OUT="$(grep -rnE \
    "import +backend\.($_N005_NAMES)\b|from +backend\.($_N005_NAMES)\b|from +backend +import +.*\b($_N005_NAMES)\b|from +\.+($_N005_NAMES)\b|from +\.+ +import +.*\b($_N005_NAMES)\b" \
    backend/monitoring 2>/dev/null || true)"
  if [ -n "$N005_OUT" ]; then
    red "  FAIL  monitoring module imports a forbidden subpackage:"
    printf '%s\n' "$N005_OUT" | sed 's/^/        /'
    FAIL=$((FAIL + 1))
  else
    green "  ok    backend/monitoring free of llm/agents/agents_team/mirofish (Line-2 pure quant)"
  fi
else
  green "  ok    no monitoring module present yet (skip)"
fi

# ----------------------------------------------------------------------
echo
yellow "[P-002] portfolio_allocation isolation (no backend.{llm,agents,mirofish}; not imported by backend/risk)"
# The config-layer allocation module is pure upstream (P0-7-amendment-2026-05-30
# §4): it must not import the LLM/agents/mirofish decision path, and the
# RiskEngine (independent authority) must not import IT — allocation only
# tightens sizing, it never sits inside the risk gate.
if [ -d backend/portfolio_allocation ]; then
  P002_FAIL=0
  # Mirror the N-005 pattern set so dotted, name-level (``from backend import
  # llm``) and relative (``from ..agents import x``) import forms are ALL caught
  # — the AST pytest (tests/portfolio_allocation/test_module_contract.py) is the
  # authoritative guard; this grep is the standalone-CI fast gate (codex P-007 P2).
  _P002_NAMES='llm|agents|mirofish'
  P002_IMP="$(grep -rnE \
    "import +backend\.($_P002_NAMES)\b|from +backend\.($_P002_NAMES)\b|from +backend +import +.*\b($_P002_NAMES)\b|from +\.+($_P002_NAMES)\b|from +\.+ +import +.*\b($_P002_NAMES)\b" \
    backend/portfolio_allocation 2>/dev/null || true)"
  if [ -n "$P002_IMP" ]; then
    red "  FAIL  portfolio_allocation imports a forbidden subpackage:"
    printf '%s\n' "$P002_IMP" | sed 's/^/        /'
    P002_FAIL=1
  fi
  P002_RISK="$(grep -rn 'portfolio_allocation' backend/risk 2>/dev/null || true)"
  if [ -n "$P002_RISK" ]; then
    red "  FAIL  backend/risk imports portfolio_allocation (must stay upstream-only):"
    printf '%s\n' "$P002_RISK" | sed 's/^/        /'
    P002_FAIL=1
  fi
  if [ "$P002_FAIL" -eq 0 ]; then
    green "  ok    portfolio_allocation free of llm/agents/mirofish + not imported by backend/risk"
  else
    FAIL=$((FAIL + 1))
  fi
else
  green "  ok    no portfolio_allocation module present yet (skip)"
fi

# ----------------------------------------------------------------------
echo
yellow "[P-004] FeishuMessageKind locked at 6 (basket_digest, P0-3-amendment-2026-05-30)"
FMK_OUT="$(python3 - <<'PY' 2>/dev/null || echo "SCANNER_ERROR"
from backend.integrations.feishu.renderer import FeishuMessageKind

kinds = {k.value for k in FeishuMessageKind}
expected = {
    "instruction_plan", "clarification", "reconciliation_request",
    "reconciliation_result", "alert", "basket_digest",
}
print("OK" if kinds == expected else f"MISMATCH:{sorted(kinds)}")
PY
)"
if [ "$FMK_OUT" = "OK" ]; then
  green "  ok    FeishuMessageKind has exactly the 6 locked members (incl basket_digest)"
else
  red "  FAIL  FeishuMessageKind != the 6 locked members: $FMK_OUT"
  FAIL=$((FAIL + 1))
fi

# ----------------------------------------------------------------------
# V-002 / P0-7-amendment-2026-06-01 — slot_portfolio rotation layer.
# The deterministic ≤5-slot rotation module must:
#   1. NOT import backend.{llm,agents,mirofish} (pure quant — the decision uses
#      only Line-1 quant + deterministic Line-2 health; §1.6 decoupling).
#   2. NEVER construct an InstructionPlan (R0 §4 single construction point — it
#      only proposes / records intent; the SELL/BUY go through the builder).
# The M-004 AST scan already forbids InstructionPlan construction across ALL of
# backend/; this block asserts the slot_portfolio-specific closure explicitly so
# a regression here is pinned to V-002. Paired with
# tests/slot_portfolio/test_module_contract.py (authoritative AST guard).
# ----------------------------------------------------------------------
echo
yellow "[V-002] slot_portfolio isolation + no InstructionPlan construction"
V002_OUT="$(python3 - <<'PY' 2>/dev/null || echo "SCANNER_ERROR"
import ast
import pathlib
import sys

ROOT = pathlib.Path("backend/slot_portfolio")
FORBIDDEN = {"llm", "agents", "mirofish"}
violations: list[str] = []

if not ROOT.exists():
    print("")  # module not present yet — skip cleanly
    sys.exit(0)

for path in sorted(ROOT.rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        continue
    # 1. import isolation (dotted / name-level / relative).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if len(parts) >= 2 and parts[0] == "backend" and parts[1] in FORBIDDEN:
                    violations.append(f"{path}:{node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".") if mod else []
            if (
                node.level == 0 and len(parts) >= 2
                and parts[0] == "backend" and parts[1] in FORBIDDEN
            ):
                violations.append(f"{path}:{node.lineno}: from {mod} import ...")
            if node.level == 0 and mod == "backend":
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: from backend import {a.name}")
            if node.level > 0 and parts and parts[0] in FORBIDDEN:
                violations.append(f"{path}:{node.lineno}: relative import of forbidden")
            if node.level > 0 and not mod:
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: relative import {a.name}")
    # 2. no InstructionPlan construction (alias-aware, mirrors M-004).
    names = {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.name == "InstructionPlan"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            violations.append(f"{path}:{node.lineno}: InstructionPlan(...)")
        elif isinstance(f, ast.Attribute) and f.attr == "InstructionPlan":
            violations.append(f"{path}:{node.lineno}: *.InstructionPlan(...)")

if violations:
    print("\n".join(violations))
PY
)"
# Dispatch on OUTPUT non-emptiness (mirrors [M-004]) — NOT on the exit code:
# ``$(... || echo SCANNER_ERROR)`` masks a Python ``sys.exit(1)`` (the
# substitution status becomes echo's 0), so the prior RC-based check reported a
# real violation as a pass (codex W-001 P2 — same latent bug here). The scanner
# prints violations + exits 0; ``|| echo`` now fires ONLY on an interpreter crash.
if [ "$V002_OUT" = "SCANNER_ERROR" ]; then
  red "  FAIL  [V-002] slot_portfolio AST scanner error"
  FAIL=$((FAIL + 1))
elif [ -n "$V002_OUT" ]; then
  red "  FAIL  slot_portfolio isolation / InstructionPlan red line violated:"
  printf '%s\n' "$V002_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
else
  green "  ok    slot_portfolio pure (no llm/agents/mirofish) + no InstructionPlan"
fi

# ----------------------------------------------------------------------
# W-001 / P0-10-amendment-line2-2026-06-01 — position_thesis layer.
# The deterministic PositionThesis derivation module must:
#   1. NOT import backend.{llm,agents,agents_team,mirofish} (pure quant — the
#      LLM only writes the opaque pillar text upstream; thresholds are derived
#      from the buy-time snapshot with no LLM input, §1.1).
#   2. NEVER construct an InstructionPlan (R0 §4 — a thesis is advisory data,
#      never an order; a SELL it justifies goes through the builder).
# Paired with tests/position_thesis/test_module_contract.py (authoritative AST).
# ----------------------------------------------------------------------
echo
yellow "[W-001] position_thesis isolation + no InstructionPlan construction"
W001_OUT="$(python3 - <<'PY' 2>/dev/null || echo "SCANNER_ERROR"
import ast
import pathlib
import sys

ROOT = pathlib.Path("backend/position_thesis")
FORBIDDEN = {"llm", "agents", "agents_team", "mirofish"}
violations: list[str] = []

if not ROOT.exists():
    print("")  # module not present yet — skip cleanly
    sys.exit(0)

for path in sorted(ROOT.rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if len(parts) >= 2 and parts[0] == "backend" and parts[1] in FORBIDDEN:
                    violations.append(f"{path}:{node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".") if mod else []
            if (
                node.level == 0 and len(parts) >= 2
                and parts[0] == "backend" and parts[1] in FORBIDDEN
            ):
                violations.append(f"{path}:{node.lineno}: from {mod} import ...")
            if node.level == 0 and mod == "backend":
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: from backend import {a.name}")
            if node.level > 0 and parts and parts[0] in FORBIDDEN:
                violations.append(f"{path}:{node.lineno}: relative import of forbidden")
            if node.level > 0 and not mod:
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: relative import {a.name}")
    names = {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.name == "InstructionPlan"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            violations.append(f"{path}:{node.lineno}: InstructionPlan(...)")
        elif isinstance(f, ast.Attribute) and f.attr == "InstructionPlan":
            violations.append(f"{path}:{node.lineno}: *.InstructionPlan(...)")

if violations:
    print("\n".join(violations))
PY
)"
# Dispatch on OUTPUT non-emptiness (mirrors [M-004]/[P-004]) — NOT on the
# exit code: ``$(... || echo SCANNER_ERROR)`` masks a Python ``sys.exit(1)``
# (the substitution's status becomes echo's 0), so an RC-based check reported
# a real violation as a pass (codex W-001 P2). The scanner prints violations +
# exits 0; ``|| echo`` now fires ONLY on an actual interpreter crash.
if [ "$W001_OUT" = "SCANNER_ERROR" ]; then
  red "  FAIL  [W-001] position_thesis AST scanner error"
  FAIL=$((FAIL + 1))
elif [ -n "$W001_OUT" ]; then
  red "  FAIL  position_thesis isolation / InstructionPlan red line violated:"
  printf '%s\n' "$W001_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
else
  green "  ok    position_thesis pure (no llm/agents/mirofish) + no InstructionPlan"
fi

# Y-005 / P0-8-amendment-2026-06-01 — theme_research peer-sourcing layer.
# The ONLY LLM+web-bearing module, but LLM/web arrive via INJECTED Protocols:
# it must never hard-import the trading stack, and must never construct an
# InstructionPlan (single construction point). The 0-LLM modules importing it
# back is caught by tests/theme_research/test_module_contract.py.
yellow "[Y-005] theme_research isolation + no InstructionPlan construction"
Y005_OUT="$(python3 - <<'PY' 2>/dev/null || echo "SCANNER_ERROR"
import ast
import pathlib
import sys

ROOT = pathlib.Path("backend/theme_research")
FORBIDDEN = {
    "api", "broker", "risk", "llm", "agents", "agents_team",
    "mirofish", "data", "screening", "marketdata_snapshot",
}
violations: list[str] = []

if not ROOT.exists():
    print("")  # module not present yet — skip cleanly
    sys.exit(0)

for path in sorted(ROOT.rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if len(parts) >= 2 and parts[0] == "backend" and parts[1] in FORBIDDEN:
                    violations.append(f"{path}:{node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".") if mod else []
            if (
                node.level == 0 and len(parts) >= 2
                and parts[0] == "backend" and parts[1] in FORBIDDEN
            ):
                violations.append(f"{path}:{node.lineno}: from {mod} import ...")
            if node.level == 0 and mod == "backend":
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: from backend import {a.name}")
            if node.level > 0 and parts and parts[0] in FORBIDDEN:
                violations.append(f"{path}:{node.lineno}: relative import of forbidden")
            if node.level > 0 and not mod:
                for a in node.names:
                    if a.name in FORBIDDEN:
                        violations.append(f"{path}:{node.lineno}: relative import {a.name}")
    names = {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for a in node.names
        if a.name == "InstructionPlan"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            violations.append(f"{path}:{node.lineno}: InstructionPlan(...)")
        elif isinstance(f, ast.Attribute) and f.attr == "InstructionPlan":
            violations.append(f"{path}:{node.lineno}: *.InstructionPlan(...)")

if violations:
    print("\n".join(violations))
PY
)"
if [ "$Y005_OUT" = "SCANNER_ERROR" ]; then
  red "  FAIL  [Y-005] theme_research AST scanner error"
  FAIL=$((FAIL + 1))
elif [ -n "$Y005_OUT" ]; then
  red "  FAIL  theme_research isolation / InstructionPlan red line violated:"
  printf '%s\n' "$Y005_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
else
  green "  ok    theme_research pure (no trading-stack imports) + no InstructionPlan"
fi

# ----------------------------------------------------------------------
# AA-005 / P1-2.A-amendment-2026-06-12 — attribution review module
# isolation. backend/review is the objective-evidence substrate for the
# Phase AB promotion engine: pure-deterministic, zero LLM. It must never
# import backend.{llm,agents,agents_team,mirofish} and never construct
# an InstructionPlan (single construction point, R0 §4). The AST pytest
# (tests/review/test_module_contract.py) is the authoritative guard and
# this grep is the standalone-CI fast gate (mirrors the N-005 pattern).
# ----------------------------------------------------------------------
echo
yellow "[AA-005] review module isolation (no backend.{llm,agents,agents_team,mirofish}; no InstructionPlan)"
if [ -d backend/review ]; then
  AA005_FAIL=0
  _AA005_NAMES='llm|agents_team|agents|mirofish'
  AA005_IMP="$(grep -rnE \
    "import +backend\.($_AA005_NAMES)\b|from +backend\.($_AA005_NAMES)\b|from +backend +import +.*\b($_AA005_NAMES)\b|from +\.+($_AA005_NAMES)\b|from +\.+ +import +.*\b($_AA005_NAMES)\b" \
    backend/review 2>/dev/null || true)"
  if [ -n "$AA005_IMP" ]; then
    red "  FAIL  review module imports a forbidden subpackage:"
    printf '%s\n' "$AA005_IMP" | sed 's/^/        /'
    AA005_FAIL=1
  fi
  AA005_PLAN="$(grep -rn --include='*.py' 'InstructionPlan(' backend/review 2>/dev/null || true)"
  if [ -n "$AA005_PLAN" ]; then
    red "  FAIL  review module constructs InstructionPlan (single construction point):"
    printf '%s\n' "$AA005_PLAN" | sed 's/^/        /'
    AA005_FAIL=1
  fi
  if [ "$AA005_FAIL" -ne 0 ]; then
    FAIL=$((FAIL + 1))
  else
    green "  ok    backend/review pure-deterministic (no LLM stack, no InstructionPlan)"
  fi
else
  green "  ok    no review module present yet (skip)"
fi

# ----------------------------------------------------------------------
# R-002 / P2-2-amendment-2026-05-24 — rqalpha realtime isolation.
# rqalpha is a TEST-TIME differential oracle (never a second execution
# truth, never on the realtime path) with a NOASSERTION license (never
# vendored). The string must stay confined to the oracle adapter; the
# AST pytest (tests/strategy_evolution/test_module_contract.py) is the
# authoritative guard and this grep is the standalone-CI fast gate.
# ----------------------------------------------------------------------
echo
yellow "[R-002] rqalpha confined to backend/strategy_evolution/ (imports: oracle adapter only)"
# Prose mentions are allowed inside the evolution package (the sibling
# modules cross-reference the oracle); the AST contract test pins the
# actual IMPORT to backtest_oracle.py alone.
R002_OUT="$(grep -rln 'rqalpha' backend/ --include='*.py' 2>/dev/null \
  | grep -v '^backend/strategy_evolution/' || true)"
if [ -n "$R002_OUT" ]; then
  red "  FAIL  rqalpha referenced outside the oracle adapter:"
  printf '%s\n' "$R002_OUT" | sed 's/^/        /'
  FAIL=$((FAIL + 1))
else
  green "  ok    rqalpha test-time oracle only (no realtime reference)"
fi

# ----------------------------------------------------------------------
# AB-008 / P2-2-amendment-2026-06-12 — objective-promotion guardrails.
# (a) git is NOT a runtime control plane: zero git/subprocess in the
#     strategy_evolution package (codex P0-4; activation = intent +
#     manifest + next_boot.lock + controlled restart).
# (b) the promotion engine never leaks into the realtime path.
# The AST pytest (tests/strategy_evolution/test_adversarial_promotion
# .py) is the authoritative guard; this grep is the CI fast gate.
# ----------------------------------------------------------------------
echo
yellow "[AB-008] strategy_evolution zero git/subprocess + promotion engine confined"
AB008_FAIL=0
AB008_GIT="$(grep -rnE "^[^#]*\b(import +(subprocess|git)\b|from +(subprocess|git)\b)" backend/strategy_evolution --include='*.py' 2>/dev/null || true)"
if [ -n "$AB008_GIT" ]; then
  red "  FAIL  strategy_evolution imports git/subprocess (runtime control-plane red line):"
  printf '%s\n' "$AB008_GIT" | sed 's/^/        /'
  AB008_FAIL=1
fi
AB008_LEAK="$(grep -rln 'objective_promotion' backend/ --include='*.py' 2>/dev/null | grep -v '^backend/strategy_evolution/' || true)"
if [ -n "$AB008_LEAK" ]; then
  red "  FAIL  objective_promotion referenced outside strategy_evolution:"
  printf '%s\n' "$AB008_LEAK" | sed 's/^/        /'
  AB008_FAIL=1
fi
if [ "$AB008_FAIL" -ne 0 ]; then
  FAIL=$((FAIL + 1))
else
  green "  ok    evolution package git-free + promotion engine confined to sim lane"
fi

# ----------------------------------------------------------------------
# AC-007 / P0-8-amendment-2026-06-12 — style classifier + value-line.
# (a) backend/style is a pure deterministic classifier: no LLM stack, no
#     InstructionPlan construction (the label is display-only).
# (b) the value-line factor modules stay 0-LLM (the three-tier score is
#     all deterministic PIT features + the human-pinned theme artifact).
# The AST pytest (tests/style/test_module_contract.py) is authoritative;
# this grep is the standalone-CI fast gate (mirrors the AA-005 pattern).
# ----------------------------------------------------------------------
echo
yellow "[AC-007] style + value-line isolation (no backend.{llm,agents,agents_team,mirofish}; no InstructionPlan)"
AC007_FAIL=0
_AC007_NAMES='llm|agents_team|agents|mirofish'
_AC007_DIRS='backend/style backend/screening/value_factors.py backend/screening/value_score.py'
AC007_IMP="$(grep -rnE \
  "import +backend\.($_AC007_NAMES)\b|from +backend\.($_AC007_NAMES)\b|from +backend +import +.*\b($_AC007_NAMES)\b|from +\.+($_AC007_NAMES)\b" \
  $_AC007_DIRS 2>/dev/null || true)"
if [ -n "$AC007_IMP" ]; then
  red "  FAIL  style / value-line module imports a forbidden subpackage:"
  printf '%s\n' "$AC007_IMP" | sed 's/^/        /'
  AC007_FAIL=1
fi
AC007_PLAN="$(grep -rn --include='*.py' 'InstructionPlan(' backend/style 2>/dev/null || true)"
if [ -n "$AC007_PLAN" ]; then
  red "  FAIL  style module constructs InstructionPlan (single construction point):"
  printf '%s\n' "$AC007_PLAN" | sed 's/^/        /'
  AC007_FAIL=1
fi
if [ "$AC007_FAIL" -ne 0 ]; then
  FAIL=$((FAIL + 1))
else
  green "  ok    style + value-line pure-deterministic (no LLM stack, no InstructionPlan)"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  green "All redline checks passed."
  exit 0
fi
red "$FAIL redline check(s) failed."
exit 1
