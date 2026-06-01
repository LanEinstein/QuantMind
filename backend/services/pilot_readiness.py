"""PILOT go-live readiness probe — P0-6-amendment-2026-05-25 §2.3.

The PILOT tier of the tier-aware acceptance gate
(:meth:`backend.services.acceptance_report.AcceptanceService.can_switch_to_feishu_on`
with ``target_tier="pilot"``) is ``allowed`` only when ALL 11 conditions in
amendment §2.3 are met. This module is the concrete probe behind that branch:

* **5 live-probed** at gate-eval time (deterministic runtime state):
    1.  SIM account             — active broker is the MockBroker mirror (sync)
    2.  J-007 owner authorization — ``QUANTMIND_PROD_RUN`` + valid owner auth
    8.  16:00 reconciliation     — no OPEN reconciliation ticket blocking
    9.  data-quality             — the 4 blocking breaches are clear
    10. LLM timeout + cost-guard — timeout ≤5% AND ¥20/day hard reserve active
* **6 manifest sign-offs** (``config/pilot_readiness.yaml``), conditions
    3/4/5/6/7/11 — produced by U-D3 (3) / U-D4 (4/5/6/7) + the rollback drill
    (11). These are NOT live-probeable at gate-eval time; they are
    git-committed, owner/test attestations.

EVERY check is independently **fail-closed**: a raised exception, a missing
dependency, or a falsey result counts as UNMET and names the condition in the
returned reason tuple. :meth:`PilotReadinessProbe.evaluate` returns the tuple
of unmet reasons; an empty tuple == all 11 met == PILOT ``allowed``.

The four I/O checks (reconciliation / data-quality / LLM-timeout / cost-guard)
are injected as zero-arg **async** callables; the SIM-account check is a cheap
sync callable. The probe stays decoupled from the concrete objects and is
fully unit-testable with plain lambdas / async stubs. This module is
import-clean of the LLM stack (CLAUDE.md §2.8 — the LLM never participates in
the acceptance path).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml

from backend.services.owner_authorization import (
    OwnerProdAuthorizationError,
    is_production_run,
    validate_owner_authorization,
)

log = structlog.get_logger(component="services.pilot_readiness")

DEFAULT_MANIFEST_PATH = Path("config/pilot_readiness.yaml")

# The six manifest sign-off keys (amendment §2.3 conditions 3/4/5/6/7/11),
# paired with the audit reason emitted when unmet. Locked here so a typo /
# extra / missing key in the YAML is fail-closed rather than silently treated
# as met.
_MANIFEST_KEYS: tuple[tuple[str, str], ...] = (
    ("dry_run_double_line_pass", "cond3:dry_run_double_line_pass"),
    ("feishu_send_recv_smoke_pass", "cond4:feishu_send_recv_smoke_pass"),
    ("outbox_restart_idempotent", "cond5:outbox_restart_idempotent"),
    ("no_double_execution_invariant", "cond6:no_double_execution_invariant"),
    (
        "all_report_templates_parse_apply",
        "cond7:all_report_templates_parse_apply",
    ),
    ("rollback_simulation_only_ready", "cond11:rollback_simulation_only_ready"),
)
_MANIFEST_EXPECTED_KEYS: frozenset[str] = frozenset(k for k, _ in _MANIFEST_KEYS)


def _safe_bool(fn: Callable[[], bool]) -> bool:
    """Evaluate a sync live-probe callable, treating any exception as ``False``.

    Fail-closed: a check that raises (missing wiring, transient error) must
    NEVER be read as "condition met".
    """
    try:
        return bool(fn())
    except Exception as exc:  # noqa: BLE001 — fail-closed is the contract
        log.warning("pilot_probe_check_raised", error=str(exc))
        return False


async def _safe_await(fn: Callable[[], Awaitable[bool]]) -> bool:
    """Await an async live-probe callable, treating any exception as ``False``."""
    try:
        return bool(await fn())
    except Exception as exc:  # noqa: BLE001 — fail-closed is the contract
        log.warning("pilot_probe_async_check_raised", error=str(exc))
        return False


def read_manifest_flags(path: Path) -> dict[str, bool]:
    """Read the PILOT readiness manifest, fail-closed on any anomaly.

    Returns a mapping of the six locked keys to their boolean sign-off. A
    missing file, unreadable/malformed YAML, non-mapping root, or any
    unexpected key returns ``{}`` (every condition unmet). A present key whose
    value is not a real ``bool`` is omitted (that condition unmet).
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        log.warning("pilot_manifest_missing", path=str(path))
        return {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("pilot_manifest_unreadable", path=str(path), error=str(exc))
        return {}
    if not isinstance(raw, dict):
        log.warning("pilot_manifest_not_mapping", path=str(path))
        return {}
    extra = set(raw) - _MANIFEST_EXPECTED_KEYS
    if extra:
        # Drift from the locked schema → refuse to trust the whole file.
        log.warning("pilot_manifest_unexpected_keys", extra=sorted(extra))
        return {}
    flags: dict[str, bool] = {}
    for key in _MANIFEST_EXPECTED_KEYS:
        val = raw.get(key)
        # ``isinstance(True, int)`` is True but we require a real bool so a
        # stray ``1`` cannot masquerade as a sign-off.
        if isinstance(val, bool):
            flags[key] = val
    return flags


def is_llm_timeout_rate_acceptable(
    timeouts: int, calls: int, *, ceiling: float
) -> bool:
    """cond10a live-probe policy — single transient-timeout grace + ratio.

    P0-6-amendment-2026-06-01: the PILOT **live daily** counter
    (``llm:{timeouts,calls}:{utc_date}``) is shared cross-process and not
    actually cold at go-live start, so on a low-volume morning a lone transient
    timeout (e.g. ``1/18 == 5.56%``) trips the 5% ceiling and dead-locks the
    gate (gate fails → backend never starts → denominator never grows). Healthy
    iff **at most one** timeout (a single transient blip never trips the gate)
    OR the rate is within ``ceiling``. This catches catastrophic startup
    failure even at small samples (``5/5`` → two-plus timeouts AND 100% rate →
    UNMET) while clearing the small-sample false positive. ``ceiling`` (0.05)
    and the 45-day acceptance ``llm_timeout_rate ≤ 5%`` gate are unchanged —
    this only refines the small-denominator live verdict. Cold start (0/0) is
    healthy via the ``timeouts <= 1`` branch.
    """
    if timeouts <= 1:
        return True
    return (timeouts / max(calls, 1)) <= ceiling


@dataclass(frozen=True)
class PilotReadinessProbe:
    """Concrete 11-condition PILOT readiness probe (fail-closed per condition).

    ``is_sim_broker`` is a cheap sync check; the other four live checks do I/O
    (reconciliation repo / data-quality provider / acceptance report / cost
    guard) and are injected as async callables. ``env`` is the process
    environment used for the J-007 owner-authorization check.
    """

    is_sim_broker: Callable[[], bool]
    reconciliation_clear: Callable[[], Awaitable[bool]]
    data_quality_clear: Callable[[], Awaitable[bool]]
    llm_timeout_within_ceiling: Callable[[], Awaitable[bool]]
    cost_guard_hard_reserve_active: Callable[[], Awaitable[bool]]
    env: Mapping[str, str]
    today: Callable[[], dt.date] = dt.date.today
    manifest_path: Path = field(default=DEFAULT_MANIFEST_PATH)

    async def evaluate(self) -> tuple[str, ...]:
        """Return the tuple of UNMET condition reasons (empty == all met)."""
        unmet: list[str] = []

        # cond 1 — SIM account (active broker is the MockBroker mirror).
        if not _safe_bool(self.is_sim_broker):
            unmet.append("cond1:active_broker_not_mock")

        # cond 2 — J-007 owner authorization.
        unmet.extend(self._owner_auth_unmet())

        # cond 8 — 16:00 reconciliation green (no OPEN ticket blocking).
        if not await _safe_await(self.reconciliation_clear):
            unmet.append("cond8:reconciliation_not_clear")

        # cond 9 — data-quality 4 blocking breaches clear.
        if not await _safe_await(self.data_quality_clear):
            unmet.append("cond9:data_quality_blocking_breach")

        # cond 10 — LLM timeout ≤5% AND ¥20/day hard reserve active.
        if not await _safe_await(self.llm_timeout_within_ceiling):
            unmet.append("cond10a:llm_timeout_rate_above_ceiling")
        if not await _safe_await(self.cost_guard_hard_reserve_active):
            unmet.append("cond10b:cost_guard_hard_reserve_inactive")

        # cond 3/4/5/6/7/11 — manifest sign-offs (fail-closed).
        unmet.extend(self._manifest_unmet())

        return tuple(unmet)

    def _owner_auth_unmet(self) -> list[str]:
        """cond 2 — production-run flag + valid, unexpired owner authorization.

        :class:`OwnerProdAuthorizationError` is a ``SystemExit`` subclass; it
        is caught explicitly here so a probe call never terminates the
        process — an invalid authorization is just an unmet condition.
        """
        try:
            if not is_production_run(self.env):
                return ["cond2:not_production_run"]
            validate_owner_authorization(env=self.env, today=self.today())
        except OwnerProdAuthorizationError as exc:
            return [f"cond2:owner_authorization_invalid:{exc}"]
        except Exception as exc:  # noqa: BLE001 — fail-closed
            log.warning("pilot_probe_owner_auth_raised", error=str(exc))
            return ["cond2:owner_authorization_error"]
        return []

    def _manifest_unmet(self) -> list[str]:
        flags = read_manifest_flags(self.manifest_path)
        unmet: list[str] = []
        for key, reason in _MANIFEST_KEYS:
            if flags.get(key) is not True:
                unmet.append(f"manifest:{reason}_not_signed_off")
        return unmet
