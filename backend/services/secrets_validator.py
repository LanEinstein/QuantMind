"""Secrets validator — startup fail-fast for the credential pool (P1-6 / H-001).

Two-layer guard against credential misuse:

1. ``.env`` static scan — credential-prefixed assignments
   (``DEEPSEEK_API_KEY=``, ``DASHSCOPE_API_KEY=``, ``MOONSHOT_API_KEY=``,
   ``FEISHU_*=``) are a P1-6 §1.1 red line. The .env file is for
   non-secret config only; secrets must live in ``~/.bashrc`` so they
   never enter the project tree.

2. Process environment validation — every credential the run mode
   actually needs must be present **and shaped correctly** before the
   FastAPI app accepts requests:
   * LLM pool (always required): ``DEEPSEEK_API_KEY`` / ``DASHSCOPE_API_KEY``
     / ``MOONSHOT_API_KEY`` — all must start with ``sk-`` and be at
     least 16 chars (matches the Anthropic/DeepSeek/Moonshot key shape).
   * Feishu pool (required iff ``FEISHU_INTERACTIVE_ENABLED=true``):
     ``FEISHU_APP_ID`` (``cli_`` prefix, 20 chars) / ``FEISHU_APP_SECRET``
     (32 chars) / ``FEISHU_VERIFY_TOKEN`` (32 chars) / ``FEISHU_ENCRYPT_KEY``
     (32 chars) / ``FEISHU_ALERT_CHAT_ID`` (``oc_`` prefix, 35 chars).

P0-2-amendment-2026-05-16 — owner Feishu tenant disabled the custom-bot
feature globally. The credential pool dropped from 6 → 5 (no
``FEISHU_CUSTOM_BOT_*``). Any leftover ``FEISHU_CUSTOM_BOT_WEBHOOK_URL``
or ``FEISHU_CUSTOM_BOT_SIGN_SECRET`` is a non-blocking soft warning
that surfaces via an audit event so the operator can clean the env.

Fail-fast semantics: errors raise :class:`SecretsValidationError` (a
:class:`SystemExit` subclass) so uvicorn / systemd / docker-compose
exits non-zero with a clear message. Warnings are returned for the
caller to dispatch to the audit store after the app is wired.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(component="secrets_validator")


# --- Locked credential pool (P1-6 §1.1 + P0-2-amendment-2026-05-16) -----

LLM_API_KEY_NAMES: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
)
"""3 LLM provider keys — always required regardless of run mode."""

FEISHU_CREDENTIAL_NAMES: tuple[str, ...] = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFY_TOKEN",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_ALERT_CHAT_ID",
)
"""5 Feishu credentials — required iff FEISHU_INTERACTIVE_ENABLED=true.
Custom-bot credentials intentionally absent (P0-2-amendment-2026-05-16)."""

LEGACY_FEISHU_CUSTOM_BOT_NAMES: tuple[str, ...] = (
    "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
    "FEISHU_CUSTOM_BOT_SIGN_SECRET",
)
"""Soft-warning legacy names — owner tenant disables custom-bot, so any
value here is dead config but not a startup blocker."""

HETEROGENEOUS_CREDENTIAL_NAMES: tuple[str, ...] = (
    "GITHUB_TOKEN",  # X-010 GitHub releases crawler PAT (Q3 — not in LLM/Feishu pool)
    "TUSHARE_TOKEN",  # K-001 Tushare Pro data source (P0-8-amendment-2026-05-24)
)
"""Heterogeneous credentials (P2-2 §1.13 Q3 / P0-8-amendment-2026-05-24):
credentials that are NOT part of the LLM 3 + Feishu 5 pool but that
``secrets_validator`` still inspects with a *soft* warning at boot.

* ``GITHUB_TOKEN`` — Q3 of P2-2-implementation-plan-2026-05-18 keeps it
  outside the canonical pool because it serves a different purpose
  (crawler auth, not LLM / chat), is owned by GitHub not by an internal
  vendor, and has its own rotation cadence.
* ``TUSHARE_TOKEN`` — K-001 full-market data source token. Owned by
  Tushare (data vendor, not LLM / chat), points-based sponsorship with
  its own cadence; data cost has no ceiling (P1-7), so it never enters
  the LLM budget either.

We still soft-warn at boot when the env var is set but the value does
not look right (per-credential shape regex) so an obvious paste error
surfaces before the downstream call fails. Absence is *not* a warning
here — the consuming client fails fast if its source is enabled."""

_GITHUB_PAT_RE = re.compile(r"\A(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\Z")
"""GitHub PAT shape — covers both classic (``ghp_``) and fine-grained
(``github_pat_``) formats. Anchored so trailing whitespace is rejected."""

_TUSHARE_TOKEN_RE = re.compile(r"\A[A-Za-z0-9]{32,}\Z")
"""Tushare token shape — a long (≥32) alphanumeric string. Anchored so
a stray space / quote / obviously-wrong paste is flagged."""

_HETEROGENEOUS_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "GITHUB_TOKEN": _GITHUB_PAT_RE,
    "TUSHARE_TOKEN": _TUSHARE_TOKEN_RE,
}

# Total pool size = 3 LLM + 5 Feishu = 8 names. The two CUSTOM_BOT_*
# legacy names and the heterogeneous credentials are explicitly excluded.
EXPECTED_POOL_SIZE = 8


# Regex / length checks. Anchored ``\A...\Z`` so a stray space or quote
# fails the shape check immediately rather than silently mismatching.
_LLM_KEY_RE = re.compile(r"\Ask-[A-Za-z0-9_\-]{16,}\Z")
_FEISHU_APP_ID_RE = re.compile(r"\Acli_[A-Za-z0-9]{16}\Z")  # cli_ + 16 chars = 20
_FEISHU_SECRET32_RE = re.compile(r"\A[A-Za-z0-9]{32}\Z")
_FEISHU_CHAT_ID_RE = re.compile(r"\Aoc_[A-Za-z0-9]{32}\Z")  # oc_ + 32 chars = 35

_FEISHU_CREDENTIAL_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "FEISHU_APP_ID": _FEISHU_APP_ID_RE,
    "FEISHU_APP_SECRET": _FEISHU_SECRET32_RE,
    "FEISHU_VERIFY_TOKEN": _FEISHU_SECRET32_RE,
    "FEISHU_ENCRYPT_KEY": _FEISHU_SECRET32_RE,
    "FEISHU_ALERT_CHAT_ID": _FEISHU_CHAT_ID_RE,
}


# .env assignment-form scanner. Comments (lines beginning with ``#``,
# possibly after whitespace) are skipped so the .env.example docstring
# does not trip the guard. The pattern covers (1) LLM provider keys,
# (2) any FEISHU_* prefix (catches legacy custom-bot names too), and
# (3) heterogeneous credentials such as ``GITHUB_TOKEN`` — the latter
# follows the same "secrets live in ~/.bashrc, never in .env" rule
# (codex X-027 R4 follow-up — P1 finding fix).
_ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<name>(?:DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|"
    r"FEISHU_[A-Z_]+|GITHUB_TOKEN|TUSHARE_TOKEN))\s*="
)

_FEISHU_FLAG_ENV = "FEISHU_INTERACTIVE_ENABLED"
_TRUTHY_TOKENS = frozenset({"true", "1", "yes", "on"})


# --- DTOs --------------------------------------------------------------


@dataclass(frozen=True)
class _DeferredWarning:
    """Soft warning surfaced after the audit store is wired.

    These do not block startup. The caller (main.py lifespan) walks the
    list once :class:`backend.audit.store.AuditStore` is constructed
    and writes one :class:`AuditEvent` per warning with
    ``actor=SYSTEM`` / ``outcome=DEGRADED``.
    """

    reason_namespace: str
    resource_type: str
    resource_id: str
    payload: dict[str, str]


@dataclass(frozen=True)
class SecretsValidationResult:
    """Outcome of :meth:`SecretsValidator.validate`.

    A successful validation returns a result with ``errors==()`` and
    fingerprints for every required credential. ``warnings`` carries
    deferred audit events the caller dispatches once the audit store
    is online.
    """

    fingerprints: Mapping[str, str]
    warnings: tuple[_DeferredWarning, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


class SecretsValidationError(SystemExit):
    """Raised when startup must abort.

    Subclasses :class:`SystemExit` so uvicorn / docker-compose exits
    non-zero with the assembled error message. The constructor formats
    a multi-line summary so the operator sees every missing/malformed
    credential at once, not just the first one.
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        body = "\n  - ".join(errors)
        super().__init__(
            "Refusing to start: secrets_validator blocked startup "
            f"({len(errors)} error(s)):\n  - {body}\n"
            "Fix: export the missing credentials in ~/.bashrc and reopen "
            "the shell, then retry. See docs/runbook/secrets-incident-response.md."
        )
        self.errors = errors


# --- Public helpers ---------------------------------------------------


def compute_fingerprint(value: str) -> str:
    """Return the SHA256[:8] hex fingerprint used in audit payloads.

    P1-6 §1.2 forbids plaintext credential values from reaching audit
    logs. Eight hex chars is enough for correlation without leaking
    enough material for offline brute force.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _read_env_flag(name: str, env: Mapping[str, str]) -> bool:
    return env.get(name, "").strip().lower() in _TRUTHY_TOKENS


# --- Validator --------------------------------------------------------


class SecretsValidator:
    """Startup credential gate (P1-6 / H-001).

    The validator is **stateless** — every call to :meth:`validate`
    re-reads the environment so tests can monkey-patch
    ``os.environ`` between cases without instance reuse.

    Args:
        env: mapping used for the process env scan. Defaults to
            ``os.environ`` so production startup needs no plumbing;
            tests inject a dict for hermeticity.
        env_file: path to the project ``.env`` to scan for forbidden
            assignment-form lines. Missing file is OK (the scan is
            skipped silently — .env is optional).
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = None,
    ) -> None:
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._env_file: Path = (
            env_file if env_file is not None else Path(".env")
        )

    def validate(self) -> SecretsValidationResult:
        """Return the validation result, never raises.

        Callers (typically main.py) inspect ``result.errors`` and raise
        :class:`SecretsValidationError` themselves. Keeping ``validate``
        non-raising lets tests assert on the structured result.
        """
        errors: list[str] = []
        warnings: list[_DeferredWarning] = []
        fingerprints: dict[str, str] = {}

        errors.extend(self._scan_env_file())
        errors.extend(self._validate_llm_keys(fingerprints))

        if _read_env_flag(_FEISHU_FLAG_ENV, self._env):
            errors.extend(self._validate_feishu_credentials(fingerprints))

        warnings.extend(self._collect_legacy_custom_bot_warnings())
        warnings.extend(self._scan_heterogeneous_credentials())

        return SecretsValidationResult(
            fingerprints=fingerprints,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    # -- .env scan ------------------------------------------------------

    def _scan_env_file(self) -> list[str]:
        """Reject credential-prefixed assignments in .env (P1-6 §1.1).

        Comments (``# ...``) explaining which env vars to set in
        ``~/.bashrc`` are intentional and allowed; only assignment-form
        lines fail the guard.
        """
        path = self._env_file
        if not path.exists():
            return []
        violations: list[str] = []
        for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                continue
            match = _ASSIGNMENT_RE.match(raw)
            if match is None:
                continue
            name = match.group("name")
            violations.append(
                f".env line {lineno}: forbidden credential assignment "
                f"{name!r} — move to ~/.bashrc (P1-6 §1.1)"
            )
        return violations

    # -- LLM keys -------------------------------------------------------

    def _validate_llm_keys(
        self, fingerprints: dict[str, str]
    ) -> list[str]:
        errors: list[str] = []
        for name in LLM_API_KEY_NAMES:
            value = self._env.get(name, "").strip()
            if not value:
                errors.append(
                    f"required LLM credential {name!r} is missing from "
                    "process env — export it in ~/.bashrc"
                )
                continue
            if not _LLM_KEY_RE.match(value):
                errors.append(
                    f"LLM credential {name!r} fails shape check "
                    "(expected ^sk-[A-Za-z0-9_-]{16,}$)"
                )
                continue
            fingerprints[name] = compute_fingerprint(value)
        return errors

    # -- Feishu credentials --------------------------------------------

    def _validate_feishu_credentials(
        self, fingerprints: dict[str, str]
    ) -> list[str]:
        errors: list[str] = []
        for name in FEISHU_CREDENTIAL_NAMES:
            value = self._env.get(name, "").strip()
            if not value:
                errors.append(
                    f"required Feishu credential {name!r} is missing "
                    "(FEISHU_INTERACTIVE_ENABLED=true)"
                )
                continue
            pattern = _FEISHU_CREDENTIAL_PATTERNS[name]
            if not pattern.match(value):
                errors.append(
                    f"Feishu credential {name!r} fails shape check "
                    f"(expected {pattern.pattern})"
                )
                continue
            fingerprints[name] = compute_fingerprint(value)
        return errors

    # -- Heterogeneous credentials -------------------------------------

    def _scan_heterogeneous_credentials(self) -> list[_DeferredWarning]:
        """Soft-warn on misshapen heterogeneous credentials.

        Heterogeneous credentials (e.g. ``GITHUB_TOKEN``) are NOT part
        of the LLM 3 + Feishu 5 pool — they don't count toward
        ``EXPECTED_POOL_SIZE``. We only emit a warning when the env
        var is set but the value fails the per-credential shape regex
        so an obviously broken paste surfaces early. Absence is not a
        warning here — the crawler-side init is the place to fail-fast
        if the source is enabled without the env var (codex X-027 R4
        claim 1+2).
        """
        warnings: list[_DeferredWarning] = []
        for name in HETEROGENEOUS_CREDENTIAL_NAMES:
            value = self._env.get(name, "").strip()
            if not value:
                continue
            pattern = _HETEROGENEOUS_PATTERNS.get(name)
            if pattern is not None and pattern.match(value):
                continue  # well-formed; no warning
            warnings.append(
                _DeferredWarning(
                    reason_namespace="malformed_heterogeneous_credential",
                    resource_type="credential",
                    resource_id=name,
                    payload={
                        "credential_name": name,
                        "fingerprint": compute_fingerprint(value),
                        "expected_shape": (
                            pattern.pattern
                            if pattern is not None
                            else "<unknown>"
                        ),
                    },
                )
            )
        return warnings

    # -- Legacy custom-bot warnings ------------------------------------

    def _collect_legacy_custom_bot_warnings(
        self,
    ) -> list[_DeferredWarning]:
        warnings: list[_DeferredWarning] = []
        for name in LEGACY_FEISHU_CUSTOM_BOT_NAMES:
            value = self._env.get(name, "").strip()
            if not value:
                continue
            warnings.append(
                _DeferredWarning(
                    reason_namespace="unexpected_legacy_feishu_custom_bot_credential",
                    resource_type="credential",
                    resource_id=name,
                    payload={
                        "credential_name": name,
                        "fingerprint": compute_fingerprint(value),
                        "amendment": "P0-2-amendment-2026-05-16",
                    },
                )
            )
        return warnings


def dispatch_warnings_to_jsonl(
    warnings: tuple[_DeferredWarning, ...],
    *,
    jsonl_path: Path = Path("logs/audit.jsonl"),
) -> int:
    """Write soft-warning audit events directly to ``logs/audit.jsonl``.

    Used at startup before :class:`backend.audit.store.AuditStore`
    (wired in H-002) is alive. Each warning becomes one
    :class:`AuditEvent` with ``event_type=SYSTEM_INTERRUPTED`` +
    ``actor=SYSTEM`` + ``outcome=DEGRADED`` so the audit trail shows
    the operator left a legacy credential in the env even though the
    custom-bot pool was retired (P0-2-amendment-2026-05-16).

    The JSONL leg is the dependable layer (P1-6 §1.7.4); once the
    Mongo audit collection is online, ``read_jsonl`` reconciles. We do
    not block startup if writing the JSONL fails — credential exposure
    is the bigger risk than a missed warning line.

    Returns the number of events successfully written.
    """
    if not warnings:
        return 0
    # Lazy import: keeps secrets_validator import cheap when called by
    # CLI tools that have no audit dependency.
    from datetime import UTC, datetime

    from backend.audit.models import (
        AuditActor,
        AuditEvent,
        AuditEventType,
        AuditOutcome,
    )

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with jsonl_path.open("a", encoding="utf-8") as f:
            for w in warnings:
                event = AuditEvent(
                    timestamp=datetime.now(UTC),
                    event_type=AuditEventType.SYSTEM_INTERRUPTED,
                    actor=AuditActor.SYSTEM,
                    actor_detail="secrets_validator",
                    resource_type=w.resource_type,
                    resource_id=w.resource_id,
                    payload=dict(w.payload),
                    outcome=AuditOutcome.DEGRADED,
                    reason_namespace=w.reason_namespace,
                )
                f.write(event.model_dump_json() + "\n")
                written += 1
    except OSError as exc:
        log.warning(
            "secrets_validator_audit_jsonl_failed",
            error=str(exc),
            jsonl_path=str(jsonl_path),
        )
    return written


def assert_secrets_or_exit(
    validator: SecretsValidator | None = None,
    *,
    audit_jsonl_path: Path | None = None,
) -> SecretsValidationResult:
    """Run the validator and ``SystemExit`` on any error.

    Convenience wrapper for ``main.py`` so the lifespan call site is a
    single line. Tests use :class:`SecretsValidator` directly and assert
    on :class:`SecretsValidationResult`.

    Soft warnings (e.g. legacy ``FEISHU_CUSTOM_BOT_*`` envs) are
    immediately written to ``logs/audit.jsonl`` so the operator sees
    them in the audit trail without waiting on AuditStore wiring.
    """
    actual = validator or SecretsValidator()
    result = actual.validate()
    if not result.ok:
        log.error(
            "secrets_validator_blocked",
            error_count=len(result.errors),
            errors=list(result.errors),
        )
        raise SecretsValidationError(result.errors)
    if result.warnings:
        path = audit_jsonl_path or Path("logs/audit.jsonl")
        dispatched = dispatch_warnings_to_jsonl(
            result.warnings, jsonl_path=path
        )
        log.warning(
            "secrets_validator_soft_warning",
            warning_count=len(result.warnings),
            audit_events_written=dispatched,
            warnings=[
                {
                    "resource_id": w.resource_id,
                    "reason_namespace": w.reason_namespace,
                }
                for w in result.warnings
            ],
        )
    log.info(
        "secrets_validator_ok",
        credential_count=len(result.fingerprints),
        warning_count=len(result.warnings),
        fingerprints=dict(result.fingerprints),
    )
    return result


__all__ = [
    "EXPECTED_POOL_SIZE",
    "FEISHU_CREDENTIAL_NAMES",
    "HETEROGENEOUS_CREDENTIAL_NAMES",
    "LEGACY_FEISHU_CUSTOM_BOT_NAMES",
    "LLM_API_KEY_NAMES",
    "SecretsValidationError",
    "SecretsValidationResult",
    "SecretsValidator",
    "_DeferredWarning",
    "assert_secrets_or_exit",
    "compute_fingerprint",
    "dispatch_warnings_to_jsonl",
]
