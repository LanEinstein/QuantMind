"""J-002 — Cold-start smoke check helpers.

The 18 app.state slots that ``backend.main._init_orchestration_layer``
populates are the post-boot contract: each must be non-None for the
backend to operate. :data:`ORCHESTRATION_REQUIRED_SLOTS` is the locked
tuple consumed by:

* ``scripts/smoke_test_cold_start.py`` — the cold-start verification.
* ``tests/test_phase_i_001_orchestration.py`` — the I-001 must_have list.

Changing the contract requires updating both call sites at once;
keeping the tuple here is the single source of truth so a future
contributor cannot silently drop a slot.

This module also exposes :func:`check_app_state` which inspects the
running ``app.state`` and returns a :class:`SmokeCheckResult`
summarising which slots are missing/None, plus
:func:`format_check_result` which renders the operator verdict.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ORCHESTRATION_REQUIRED_SLOTS: tuple[str, ...] = (
    "broker_event_store",
    "broker_snapshot_store",
    "market_meta_provider",
    "acceptance_repository",
    "acceptance_service",
    "execution_report_applier",
    "reconciliation_applier",
    "mode_router",
    "decision_ledger",
    "simulation_executor",
    "equity_point_repository",
    "equity_point_builder",
    "broker_scheduler",
    "instruction_plan_repository",
    "reconciliation_ticket_repository",
    "daily_reconciliation_store",
    "broker_snapshot_lookup",
    "execution_report_orchestrator",
    # U-D1 — Line-2 production orchestration (deterministic SELL/ADD lines).
    # The single mutually-exclusive routing edge + the two Line-2 runners must
    # be wired so the BrokerScheduler cron callbacks have something to invoke.
    # (Line-1 runner is wired in U-D1b alongside its 4-agent debate provider.)
    "instruction_dispatcher",
    "route_coordinator",
    "line2_daily_runner",
    "line2_intraday_runner",
)
"""The 22 ``_init_orchestration_layer`` slots that must be non-None
after lifespan startup (18 I-001 slots + 4 U-D1 Line-2 orchestration
slots). Order is the documented enumeration order inside
``backend.main`` so a quick visual scan against the source catches
drops."""


LIFESPAN_BASE_SLOTS: tuple[str, ...] = (
    "secrets",
    "run_mode",
    "redis",
    "llm_router",
    "audit_store",
    "alert_dispatcher",
)
"""Slots that the lifespan top wires before ``_init_orchestration_layer``
runs. The smoke check verifies these too so a missing prerequisite
fails noisily instead of cascading into a confusing orchestration
slot null."""


CONDITIONAL_SLOTS: tuple[tuple[str, str], ...] = (
    (
        "reconciliation_orchestrator",
        "FEISHU_DECISION_CHAT_ID unset — orchestrator stays None per "
        "P0-2-amendment-2026-05-16 §4 red line 7",
    ),
    (
        "feishu_client",
        "FEISHU_INTERACTIVE_ENABLED=false — simulation_auto baseline",
    ),
    (
        "feishu_alerter",
        "Feishu alert chat not wired — alerts degrade to audit-only",
    ),
    (
        "owner_authorization",
        "QUANTMIND_PROD_RUN unset — J-007 gate is a no-op in dev mode",
    ),
)
"""Slots that may legitimately be ``None`` outside production / when
optional dependencies are unwired. The smoke check reports the
condition rather than flagging the None as a failure."""


@dataclass(frozen=True)
class SmokeCheckResult:
    """Outcome of :func:`check_app_state`."""

    missing_required: tuple[str, ...]
    none_required: tuple[str, ...]
    none_conditional: tuple[tuple[str, str], ...]
    present_required: tuple[str, ...]
    llm_router_stubbed: bool | None
    """``True`` iff QUANTMIND_LLM_STUB is honoured by the wired router;
    ``False`` when a real router is wired; ``None`` when the LLM router
    is missing entirely (also reflected in ``none_required``)."""

    @property
    def ok(self) -> bool:
        """No missing / None required slots."""
        return not self.missing_required and not self.none_required


def check_app_state(
    state: Any,
    *,
    required: Iterable[str] = ORCHESTRATION_REQUIRED_SLOTS,
    base_slots: Iterable[str] = LIFESPAN_BASE_SLOTS,
    conditional: Iterable[tuple[str, str]] = CONDITIONAL_SLOTS,
) -> SmokeCheckResult:
    """Inspect a Starlette/FastAPI ``app.state`` after lifespan boot.

    Args:
        state: typically ``application.state`` after lifespan completes.
        required: slot names that must be non-None.
        base_slots: lifespan-top slots verified ahead of orchestration.
        conditional: slot names that may be None for documented reasons.

    Returns the :class:`SmokeCheckResult` summary. The caller uses
    ``ok`` to decide exit code and :func:`format_check_result` to
    render a multi-line operator verdict.
    """
    expected = tuple(base_slots) + tuple(required)
    missing: list[str] = []
    none_present: list[str] = []
    present: list[str] = []
    for name in expected:
        if not hasattr(state, name):
            missing.append(name)
            continue
        value = getattr(state, name)
        if value is None:
            none_present.append(name)
            continue
        present.append(name)

    cond_results: list[tuple[str, str]] = []
    for name, reason in conditional:
        value = getattr(state, name, None)
        if value is None:
            cond_results.append((name, reason))

    llm_stubbed: bool | None
    llm_router = getattr(state, "llm_router", None)
    if llm_router is None:
        llm_stubbed = None
    else:
        # Late import — avoid pulling the heavy LLMRouter module at the
        # top of smoke_check so a redline-check sweep on this file stays
        # cheap.
        from backend.llm.router import is_llm_stub_enabled

        llm_stubbed = is_llm_stub_enabled()

    return SmokeCheckResult(
        missing_required=tuple(missing),
        none_required=tuple(none_present),
        none_conditional=tuple(cond_results),
        present_required=tuple(present),
        llm_router_stubbed=llm_stubbed,
    )


def format_check_result(result: SmokeCheckResult) -> str:
    """Render :class:`SmokeCheckResult` as a multi-line operator verdict."""
    lines: list[str] = []
    verdict = "PASS" if result.ok else "FAIL"
    lines.append(f"smoke check verdict: {verdict}")
    lines.append(
        f"  required slots present : {len(result.present_required)}"
    )
    lines.append(
        f"  required slots missing : {len(result.missing_required)}"
    )
    lines.append(
        f"  required slots None    : {len(result.none_required)}"
    )
    lines.append(
        f"  conditional slots None : {len(result.none_conditional)}"
    )
    lines.append(f"  llm_router_stubbed     : {result.llm_router_stubbed}")
    if result.missing_required:
        lines.append("  -- missing slots --")
        for name in result.missing_required:
            lines.append(f"    - {name}")
    if result.none_required:
        lines.append("  -- None required slots --")
        for name in result.none_required:
            lines.append(f"    - {name}")
    if result.none_conditional:
        lines.append("  -- documented None (informational) --")
        for name, reason in result.none_conditional:
            lines.append(f"    - {name}: {reason}")
    return "\n".join(lines)


__all__ = [
    "CONDITIONAL_SLOTS",
    "LIFESPAN_BASE_SLOTS",
    "ORCHESTRATION_REQUIRED_SLOTS",
    "SmokeCheckResult",
    "check_app_state",
    "format_check_result",
]
