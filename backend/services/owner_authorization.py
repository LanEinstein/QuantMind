"""J-007 — Owner production-run authorization gate.

Two-layer protection in front of I-002 (the 45-trading-day acceptance
window that burns real LLM budget):

1. ``QUANTMIND_PROD_RUN=1`` env var marks the process as a production
   run. When absent, this module is a no-op so the J-002 cold-start
   smoke test + J-005 N-day simulator harness keep working with stub
   LLMs.

2. ``QUANTMIND_OWNER_PROD_AUTHORIZATION=<owner_id>:YYYYMMDD`` is the
   explicit owner sign-off. Format ``^[A-Za-z0-9_\\-]+:\\d{8}$``.
   Authorizations older than 7 days are rejected so an old shell
   export cannot silently re-arm production after the owner intended
   to take the system back to staging.

Fail-fast semantics mirror :mod:`backend.services.secrets_validator` —
errors raise :class:`OwnerProdAuthorizationError` (a
:class:`SystemExit` subclass) so uvicorn / systemd / docker-compose
exits non-zero with a clear message.

Successful authorization writes one
``OWNER_PROD_AUTHORIZATION_GRANTED`` audit event directly to the
JSONL backup (the audit store is constructed later in the lifespan),
so the authorization is auditable even when Mongo is unreachable.

This gate is the **second** lock in front of P0-6 §2 red line 5
(:meth:`AcceptanceService.can_switch_to_feishu_on`). The acceptance
gate proves the system is *ready*; the owner authorization proves
the owner *intends* to spend real LLM budget on the 45-day window.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(component="owner_authorization")


PROD_RUN_ENV = "QUANTMIND_PROD_RUN"
OWNER_AUTH_ENV = "QUANTMIND_OWNER_PROD_AUTHORIZATION"

AUTHORIZATION_VALID_DAYS = 7
"""Authorization expires after 7 days. An old shell export cannot
re-arm production after the owner intended to take it back to staging."""

_AUTH_FORMAT_RE = re.compile(
    r"\A(?P<owner>[A-Za-z0-9_\-]+):(?P<date>\d{8})\Z"
)
_TRUTHY_TOKENS = frozenset({"true", "1", "yes", "on"})


@dataclass(frozen=True)
class OwnerAuthorization:
    """Successfully parsed authorization envelope."""

    raw: str
    owner_identifier: str
    granted_date: dt.date


class OwnerProdAuthorizationError(SystemExit):
    """Raised when the production-run gate refuses to start the app."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "Refusing to start: J-007 owner production-run authorization gate "
            f"blocked startup — {reason}\n"
            f"Fix: export {OWNER_AUTH_ENV}=<owner_id>:YYYYMMDD with a date "
            f"no more than {AUTHORIZATION_VALID_DAYS} days old, then restart. "
            "See docs/runbook/i-002-production-runbook.md §owner-auth."
        )
        self.reason = reason


def is_production_run(env: Mapping[str, str] | None = None) -> bool:
    """Return True iff ``QUANTMIND_PROD_RUN`` is set to a truthy token."""
    actual = env if env is not None else os.environ
    return actual.get(PROD_RUN_ENV, "").strip().lower() in _TRUTHY_TOKENS


def parse_authorization(raw: str) -> OwnerAuthorization:
    """Parse the raw env var value or raise :class:`ValueError`."""
    match = _AUTH_FORMAT_RE.match(raw)
    if match is None:
        raise ValueError(
            "value does not match ^[A-Za-z0-9_-]+:YYYYMMDD$: "
            f"{raw!r}"
        )
    owner = match.group("owner")
    date_raw = match.group("date")
    try:
        granted = dt.date(
            int(date_raw[:4]),
            int(date_raw[4:6]),
            int(date_raw[6:]),
        )
    except ValueError as exc:
        raise ValueError(
            f"invalid YYYYMMDD date {date_raw!r}: {exc}"
        ) from exc
    return OwnerAuthorization(
        raw=raw,
        owner_identifier=owner,
        granted_date=granted,
    )


def validate_owner_authorization(
    *,
    env: Mapping[str, str] | None = None,
    today: dt.date | None = None,
) -> OwnerAuthorization:
    """Validate the env var and return the parsed authorization or raise.

    Caller must have already confirmed :func:`is_production_run`. This
    function does not re-check ``QUANTMIND_PROD_RUN`` so tests can drive
    the validation directly without staging the prod-run flag.
    """
    actual_env = env if env is not None else os.environ
    actual_today = today if today is not None else dt.date.today()
    raw = actual_env.get(OWNER_AUTH_ENV, "").strip()
    if not raw:
        raise OwnerProdAuthorizationError(
            f"{PROD_RUN_ENV} is set but {OWNER_AUTH_ENV} is missing"
        )
    try:
        auth = parse_authorization(raw)
    except ValueError as exc:
        raise OwnerProdAuthorizationError(
            f"{OWNER_AUTH_ENV} malformed — {exc}"
        ) from exc
    age_days = (actual_today - auth.granted_date).days
    if age_days < 0:
        raise OwnerProdAuthorizationError(
            f"{OWNER_AUTH_ENV} authorization date is in the future "
            f"({auth.granted_date.isoformat()}, today "
            f"{actual_today.isoformat()}) — reject as clock skew or typo"
        )
    if age_days > AUTHORIZATION_VALID_DAYS:
        raise OwnerProdAuthorizationError(
            f"{OWNER_AUTH_ENV} authorization is {age_days} days old "
            f"(granted {auth.granted_date.isoformat()}, today "
            f"{actual_today.isoformat()}) — expired (max "
            f"{AUTHORIZATION_VALID_DAYS} days). Re-issue a fresh "
            "authorization to start production."
        )
    return auth


def write_authorization_audit_jsonl(
    auth: OwnerAuthorization,
    *,
    fingerprints: Mapping[str, str] | None = None,
    jsonl_path: Path = Path("logs/audit.jsonl"),
    now: dt.datetime | None = None,
) -> bool:
    """Write the OWNER_PROD_AUTHORIZATION_GRANTED audit event to JSONL.

    The audit store is constructed later in the lifespan; writing
    directly to JSONL keeps the event durable even when Mongo is down,
    matching the secrets_validator soft-warning pattern.

    Returns True on success, False when an :class:`OSError` prevented
    the write. Failure is logged but does not raise — the owner auth
    is the gate; an audit-log hiccup must not block startup.
    """
    from datetime import UTC, datetime

    from backend.audit.models import (
        AuditActor,
        AuditEvent,
        AuditEventType,
        AuditOutcome,
    )

    actual_now = now if now is not None else datetime.now(UTC)
    fingerprint_summary = sorted(fingerprints.keys()) if fingerprints else []
    payload: dict[str, str | list[str]] = {
        "owner_identifier": auth.owner_identifier,
        "granted_date": auth.granted_date.isoformat(),
        "env_var_name": OWNER_AUTH_ENV,
        "credential_pool_fingerprint_names": fingerprint_summary,
    }
    try:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        event = AuditEvent(
            timestamp=actual_now,
            event_type=AuditEventType.OWNER_PROD_AUTHORIZATION_GRANTED,
            actor=AuditActor.SYSTEM,
            actor_detail="owner_authorization",
            resource_type="production_run_gate",
            resource_id=auth.owner_identifier,
            payload=payload,
            outcome=AuditOutcome.SUCCESS,
            reason_namespace="owner_prod_authorization",
        )
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        return True
    except OSError as exc:
        log.warning(
            "owner_authorization_audit_jsonl_failed",
            error=str(exc),
            jsonl_path=str(jsonl_path),
        )
        return False


def assert_owner_authorization_or_exit(
    *,
    env: Mapping[str, str] | None = None,
    today: dt.date | None = None,
    fingerprints: Mapping[str, str] | None = None,
    audit_jsonl_path: Path | None = None,
) -> OwnerAuthorization | None:
    """Run the gate. No-op outside production. Raises on failure.

    Returns the parsed authorization when production mode is enabled
    and the env var is valid. Returns ``None`` when not in production
    mode (so J-002 / J-005 dev harnesses keep working).
    """
    if not is_production_run(env=env):
        log.info("owner_authorization_skipped", reason="not_production_run")
        return None
    auth = validate_owner_authorization(env=env, today=today)
    path = audit_jsonl_path or Path("logs/audit.jsonl")
    write_authorization_audit_jsonl(
        auth,
        fingerprints=fingerprints,
        jsonl_path=path,
    )
    log.info(
        "owner_authorization_ok",
        owner_identifier=auth.owner_identifier,
        granted_date=auth.granted_date.isoformat(),
        valid_days=AUTHORIZATION_VALID_DAYS,
    )
    return auth


__all__ = [
    "AUTHORIZATION_VALID_DAYS",
    "OWNER_AUTH_ENV",
    "PROD_RUN_ENV",
    "OwnerAuthorization",
    "OwnerProdAuthorizationError",
    "assert_owner_authorization_or_exit",
    "is_production_run",
    "parse_authorization",
    "validate_owner_authorization",
    "write_authorization_audit_jsonl",
]
