"""J-007 — Unit tests for backend/services/owner_authorization.py.

Covers:

* :func:`is_production_run` truthy / falsy token recognition.
* :func:`parse_authorization` format + date validation.
* :func:`validate_owner_authorization` happy + 5 failure modes
  (missing env, malformed format, malformed date, future date, expired).
* :func:`assert_owner_authorization_or_exit` no-op when not production.
* :func:`write_authorization_audit_jsonl` writes one well-formed event.
* AuditEventType.OWNER_PROD_AUTHORIZATION_GRANTED is in the enum and
  carries the documented value.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from backend.audit.models import (
    AUDIT_EVENT_TYPES,
    AuditActor,
    AuditEventType,
    AuditOutcome,
)
from backend.services.owner_authorization import (
    AUTHORIZATION_VALID_DAYS,
    OWNER_AUTH_ENV,
    PROD_RUN_ENV,
    OwnerAuthorization,
    OwnerProdAuthorizationError,
    assert_owner_authorization_or_exit,
    is_production_run,
    parse_authorization,
    validate_owner_authorization,
    write_authorization_audit_jsonl,
)

# ---------------------------------------------------------------------------
# AuditEventType enum
# ---------------------------------------------------------------------------


def test_owner_prod_authorization_event_in_enum() -> None:
    assert (
        AuditEventType.OWNER_PROD_AUTHORIZATION_GRANTED
        in AUDIT_EVENT_TYPES
    )
    assert (
        AuditEventType.OWNER_PROD_AUTHORIZATION_GRANTED.value
        == "owner_prod_authorization_granted"
    )


# ---------------------------------------------------------------------------
# is_production_run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_is_production_run_truthy(token: str) -> None:
    assert is_production_run({PROD_RUN_ENV: token})


@pytest.mark.parametrize("token", ["", "0", "false", "no", "off", "anything"])
def test_is_production_run_falsy(token: str) -> None:
    assert not is_production_run({PROD_RUN_ENV: token})


def test_is_production_run_unset_is_falsy() -> None:
    assert not is_production_run({})


# ---------------------------------------------------------------------------
# parse_authorization
# ---------------------------------------------------------------------------


def test_parse_authorization_happy_path() -> None:
    parsed = parse_authorization("alice_owner:20260517")
    assert parsed.owner_identifier == "alice_owner"
    assert parsed.granted_date == dt.date(2026, 5, 17)
    assert parsed.raw == "alice_owner:20260517"


def test_parse_authorization_accepts_hyphens_and_underscores() -> None:
    parsed = parse_authorization("ops-team_lead-01:20260101")
    assert parsed.owner_identifier == "ops-team_lead-01"


@pytest.mark.parametrize(
    "raw",
    [
        "missing-colon",
        ":20260517",
        "alice:",
        "alice:202605",  # too short
        "alice:202605178",  # too long
        "alice owner:20260517",  # whitespace
        "alice.owner:20260517",  # dot not allowed
        "alice:2026-05-17",  # ISO not accepted
    ],
)
def test_parse_authorization_rejects_malformed(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_authorization(raw)


def test_parse_authorization_rejects_invalid_calendar_date() -> None:
    with pytest.raises(ValueError):
        parse_authorization("alice:20260230")  # Feb 30


# ---------------------------------------------------------------------------
# validate_owner_authorization
# ---------------------------------------------------------------------------


def test_validate_missing_env_raises() -> None:
    with pytest.raises(OwnerProdAuthorizationError) as exc:
        validate_owner_authorization(env={}, today=dt.date(2026, 5, 17))
    assert "missing" in exc.value.reason


def test_validate_malformed_format_raises() -> None:
    with pytest.raises(OwnerProdAuthorizationError) as exc:
        validate_owner_authorization(
            env={OWNER_AUTH_ENV: "not_a_valid_format"},
            today=dt.date(2026, 5, 17),
        )
    assert "malformed" in exc.value.reason


def test_validate_future_date_raises() -> None:
    with pytest.raises(OwnerProdAuthorizationError) as exc:
        validate_owner_authorization(
            env={OWNER_AUTH_ENV: "alice:20260601"},
            today=dt.date(2026, 5, 17),
        )
    assert "future" in exc.value.reason


def test_validate_expired_raises() -> None:
    granted_8_days_ago = dt.date(2026, 5, 9)
    today = dt.date(2026, 5, 17)
    with pytest.raises(OwnerProdAuthorizationError) as exc:
        validate_owner_authorization(
            env={OWNER_AUTH_ENV: f"alice:{granted_8_days_ago.strftime('%Y%m%d')}"},
            today=today,
        )
    assert f"{AUTHORIZATION_VALID_DAYS} days" in exc.value.reason


def test_validate_happy_path_exactly_at_expiry_passes() -> None:
    """Authorization granted exactly 7 days ago is still valid."""
    granted_7_days_ago = dt.date(2026, 5, 10)
    today = dt.date(2026, 5, 17)
    auth = validate_owner_authorization(
        env={OWNER_AUTH_ENV: f"alice:{granted_7_days_ago.strftime('%Y%m%d')}"},
        today=today,
    )
    assert auth.owner_identifier == "alice"
    assert auth.granted_date == granted_7_days_ago


def test_validate_happy_path_granted_today_passes() -> None:
    today = dt.date(2026, 5, 17)
    auth = validate_owner_authorization(
        env={OWNER_AUTH_ENV: "alice:20260517"},
        today=today,
    )
    assert auth.granted_date == today


def test_validate_strips_whitespace() -> None:
    auth = validate_owner_authorization(
        env={OWNER_AUTH_ENV: "  alice:20260517  "},
        today=dt.date(2026, 5, 17),
    )
    assert auth.owner_identifier == "alice"


# ---------------------------------------------------------------------------
# write_authorization_audit_jsonl
# ---------------------------------------------------------------------------


def test_write_authorization_audit_jsonl_emits_well_formed_event(
    tmp_path: Path,
) -> None:
    auth = OwnerAuthorization(
        raw="alice:20260517",
        owner_identifier="alice",
        granted_date=dt.date(2026, 5, 17),
    )
    target = tmp_path / "audit.jsonl"
    ok = write_authorization_audit_jsonl(
        auth,
        fingerprints={"DEEPSEEK_API_KEY": "abc12345"},
        jsonl_path=target,
        now=dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC),
    )
    assert ok is True
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event_type"] == "owner_prod_authorization_granted"
    assert event["actor"] == AuditActor.SYSTEM.value
    assert event["outcome"] == AuditOutcome.SUCCESS.value
    assert event["resource_type"] == "production_run_gate"
    assert event["resource_id"] == "alice"
    assert event["payload"]["owner_identifier"] == "alice"
    assert event["payload"]["granted_date"] == "2026-05-17"
    assert event["payload"]["env_var_name"] == OWNER_AUTH_ENV
    assert event["payload"]["credential_pool_fingerprint_names"] == [
        "DEEPSEEK_API_KEY"
    ]
    assert event["reason_namespace"] == "owner_prod_authorization"


def test_write_authorization_audit_jsonl_appends_to_existing_file(
    tmp_path: Path,
) -> None:
    auth = OwnerAuthorization(
        raw="alice:20260517",
        owner_identifier="alice",
        granted_date=dt.date(2026, 5, 17),
    )
    target = tmp_path / "audit.jsonl"
    target.write_text("preexisting line\n", encoding="utf-8")
    ok = write_authorization_audit_jsonl(auth, jsonl_path=target)
    assert ok is True
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == "preexisting line"


def test_write_authorization_audit_jsonl_handles_oserror(
    tmp_path: Path,
) -> None:
    auth = OwnerAuthorization(
        raw="alice:20260517",
        owner_identifier="alice",
        granted_date=dt.date(2026, 5, 17),
    )
    # Target a path whose parent cannot be created (use a regular file as
    # the would-be parent directory).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "audit.jsonl"
    ok = write_authorization_audit_jsonl(auth, jsonl_path=target)
    assert ok is False


# ---------------------------------------------------------------------------
# assert_owner_authorization_or_exit
# ---------------------------------------------------------------------------


def test_assert_owner_authorization_no_op_outside_production(
    tmp_path: Path,
) -> None:
    target = tmp_path / "audit.jsonl"
    result = assert_owner_authorization_or_exit(
        env={},
        today=dt.date(2026, 5, 17),
        audit_jsonl_path=target,
    )
    assert result is None
    assert not target.exists()


def test_assert_owner_authorization_validates_and_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "audit.jsonl"
    env = {
        PROD_RUN_ENV: "1",
        OWNER_AUTH_ENV: "alice:20260517",
    }
    result = assert_owner_authorization_or_exit(
        env=env,
        today=dt.date(2026, 5, 17),
        fingerprints={"DEEPSEEK_API_KEY": "abc12345"},
        audit_jsonl_path=target,
    )
    assert result is not None
    assert result.owner_identifier == "alice"
    assert target.exists()
    line = target.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["event_type"] == "owner_prod_authorization_granted"


def test_assert_owner_authorization_missing_env_raises_systemexit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "audit.jsonl"
    env = {PROD_RUN_ENV: "1"}
    with pytest.raises(SystemExit) as exc:
        assert_owner_authorization_or_exit(
            env=env,
            today=dt.date(2026, 5, 17),
            audit_jsonl_path=target,
        )
    # OwnerProdAuthorizationError is SystemExit subclass
    assert isinstance(exc.value, OwnerProdAuthorizationError)
    assert not target.exists()


def test_assert_owner_authorization_expired_raises_systemexit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "audit.jsonl"
    env = {
        PROD_RUN_ENV: "1",
        OWNER_AUTH_ENV: "alice:20260501",  # 16 days old
    }
    with pytest.raises(OwnerProdAuthorizationError) as exc:
        assert_owner_authorization_or_exit(
            env=env,
            today=dt.date(2026, 5, 17),
            audit_jsonl_path=target,
        )
    assert "expired" in exc.value.reason


def test_assert_owner_authorization_default_env_uses_os_environ() -> None:
    """No env arg → reads os.environ. Verify by clearing prod-run flag."""
    with mock.patch.dict(os.environ, {PROD_RUN_ENV: ""}, clear=False):
        result = assert_owner_authorization_or_exit()
        assert result is None
