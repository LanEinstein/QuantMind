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

echo
if [ "$FAIL" -eq 0 ]; then
  green "All redline checks passed."
  exit 0
fi
red "$FAIL redline check(s) failed."
exit 1
