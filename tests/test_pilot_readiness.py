"""U-D2 — PilotReadinessProbe tests (P0-6-amendment-2026-05-25 §2.3).

The PILOT branch of the tier-aware acceptance gate is allowed ONLY when all 11
conditions hold. These tests lock the FAIL-CLOSED contract per condition: each
live check unmet (or raising) names its reason, the manifest is fail-closed on
every anomaly, and only the fully-satisfied probe returns an empty tuple.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from backend.services.pilot_readiness import (
    PilotReadinessProbe,
    read_manifest_flags,
)

_TODAY = dt.date(2026, 5, 26)
_MANIFEST_KEYS = (
    "dry_run_double_line_pass",
    "feishu_send_recv_smoke_pass",
    "outbox_restart_idempotent",
    "no_double_execution_invariant",
    "all_report_templates_parse_apply",
    "rollback_simulation_only_ready",
)


def _aval(value: bool) -> Callable[[], Awaitable[bool]]:
    async def _inner() -> bool:
        return value

    return _inner


def _write_manifest(path: Path, **overrides: bool) -> Path:
    flags = {k: True for k in _MANIFEST_KEYS}
    flags.update(overrides)
    body = "\n".join(f"{k}: {str(v).lower()}" for k, v in flags.items())
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _env_authorized() -> dict[str, str]:
    return {
        "QUANTMIND_PROD_RUN": "1",
        "QUANTMIND_OWNER_PROD_AUTHORIZATION": "owner-1:20260524",
    }


def _all_pass_probe(tmp_path: Path, **kwargs: object) -> PilotReadinessProbe:
    # Only write the default all-true manifest when the caller did NOT supply
    # its own manifest_path — otherwise we would clobber a partial fixture
    # written to the same path.
    if "manifest_path" not in kwargs:
        kwargs["manifest_path"] = _write_manifest(
            tmp_path / "pilot_readiness.yaml"
        )
    defaults: dict[str, object] = {
        "is_sim_broker": lambda: True,
        "reconciliation_clear": _aval(True),
        "data_quality_clear": _aval(True),
        "llm_timeout_within_ceiling": _aval(True),
        "cost_guard_hard_reserve_active": _aval(True),
        "env": _env_authorized(),
        "today": lambda: _TODAY,
    }
    defaults.update(kwargs)
    return PilotReadinessProbe(**defaults)  # type: ignore[arg-type]


# -- happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_conditions_met_returns_empty(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path)
    assert await probe.evaluate() == ()


# -- live conditions, each unmet names its reason --------------------------


@pytest.mark.asyncio
async def test_non_sim_broker_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, is_sim_broker=lambda: False)
    assert "cond1:active_broker_not_mock" in await probe.evaluate()


@pytest.mark.asyncio
async def test_not_production_run_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, env={})
    assert "cond2:not_production_run" in await probe.evaluate()


@pytest.mark.asyncio
async def test_owner_authorization_expired_unmet(tmp_path: Path) -> None:
    # granted 2025-01-01, today 2026-05-26 → far past the 7-day window.
    env = {
        "QUANTMIND_PROD_RUN": "1",
        "QUANTMIND_OWNER_PROD_AUTHORIZATION": "owner-1:20250101",
    }
    probe = _all_pass_probe(tmp_path, env=env)
    reasons = await probe.evaluate()
    assert any(r.startswith("cond2:owner_authorization_invalid") for r in reasons)


@pytest.mark.asyncio
async def test_reconciliation_not_clear_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, reconciliation_clear=_aval(False))
    assert "cond8:reconciliation_not_clear" in await probe.evaluate()


@pytest.mark.asyncio
async def test_data_quality_breach_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, data_quality_clear=_aval(False))
    assert "cond9:data_quality_blocking_breach" in await probe.evaluate()


@pytest.mark.asyncio
async def test_llm_timeout_above_ceiling_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, llm_timeout_within_ceiling=_aval(False))
    assert "cond10a:llm_timeout_rate_above_ceiling" in await probe.evaluate()


@pytest.mark.asyncio
async def test_cost_guard_reserve_inactive_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(tmp_path, cost_guard_hard_reserve_active=_aval(False))
    assert "cond10b:cost_guard_hard_reserve_inactive" in await probe.evaluate()


@pytest.mark.asyncio
async def test_raising_live_check_is_fail_closed(tmp_path: Path) -> None:
    async def _boom() -> bool:
        raise RuntimeError("transient")

    def _boom_sync() -> bool:
        raise RuntimeError("transient")

    probe = _all_pass_probe(
        tmp_path, is_sim_broker=_boom_sync, reconciliation_clear=_boom
    )
    reasons = await probe.evaluate()
    assert "cond1:active_broker_not_mock" in reasons
    assert "cond8:reconciliation_not_clear" in reasons


# -- manifest conditions ---------------------------------------------------


@pytest.mark.asyncio
async def test_missing_manifest_all_six_unmet(tmp_path: Path) -> None:
    probe = _all_pass_probe(
        tmp_path, manifest_path=tmp_path / "does_not_exist.yaml"
    )
    reasons = await probe.evaluate()
    manifest_unmet = [r for r in reasons if r.startswith("manifest:")]
    assert len(manifest_unmet) == 6


@pytest.mark.asyncio
async def test_partial_manifest_signs_only_named(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path / "pilot_readiness.yaml",
        feishu_send_recv_smoke_pass=False,
        outbox_restart_idempotent=False,
    )
    probe = _all_pass_probe(tmp_path, manifest_path=path)
    reasons = await probe.evaluate()
    assert "manifest:cond4:feishu_send_recv_smoke_pass_not_signed_off" in reasons
    assert "manifest:cond5:outbox_restart_idempotent_not_signed_off" in reasons
    assert (
        "manifest:cond3:dry_run_double_line_pass_not_signed_off" not in reasons
    )


def test_extra_manifest_key_rejects_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "pilot_readiness.yaml"
    _write_manifest(path)
    path.write_text(
        path.read_text(encoding="utf-8") + "rogue_key: true\n", encoding="utf-8"
    )
    # Drift from the locked schema → trust nothing.
    assert read_manifest_flags(path) == {}


def test_non_bool_manifest_value_is_unmet(tmp_path: Path) -> None:
    path = tmp_path / "pilot_readiness.yaml"
    body = "\n".join(
        f"{k}: {'1' if k == 'dry_run_double_line_pass' else 'true'}"
        for k in _MANIFEST_KEYS
    )
    path.write_text(body + "\n", encoding="utf-8")
    flags = read_manifest_flags(path)
    # ``1`` is not a real bool → omitted; the other five are honoured.
    assert "dry_run_double_line_pass" not in flags
    assert flags["feishu_send_recv_smoke_pass"] is True


def test_non_mapping_manifest_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "pilot_readiness.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert read_manifest_flags(path) == {}


def test_committed_manifest_is_all_false_fail_closed() -> None:
    # The repo's checked-in manifest must ship fail-closed (no premature
    # sign-off) — every condition false until its evidence lands.
    flags = read_manifest_flags(Path("config/pilot_readiness.yaml"))
    assert set(flags) == set(_MANIFEST_KEYS)
    assert all(v is False for v in flags.values())
