"""Run-mode interpretation layer (P0-1 + U-B2 mutual-exclusion fix).

This module is the single source of truth for whether the running process
backs the ``simulation_auto`` baseline and whether the
``feishu_interactive`` human-in-loop routing target is active. It replaces
the legacy ``AUTHORIZATION_MODE`` × ``QUANTMIND_PHASE`` matrix that was
tied to a real-broker live path (see
``docs/decisions/project_run_mode_p0_1.md``).

Behavior summary:
- ``simulation_auto`` is always running — it backs MockBroker + the
  acceptance loop and is the only mode that needs zero external auth.
- ``FEISHU_INTERACTIVE_ENABLED=true`` enables the Feishu human-in-loop
  routing target. The 45-trading-day acceptance gate guards the actual
  switch; this module only reads env and reports what is configured.

**Mutual-exclusion routing (U-B2, Codex P0 #4):** the two run modes are
*mutually exclusive outbound routing targets*, NOT an overlay. The legacy
wording "feishu overlay on top of the simulation route" was wrong — taken
literally it auto-fills a VALIDATED plan in simulation *and* fans it out
to the owner for manual execution, double-executing the same plan. The
correct contract: a VALIDATED InstructionPlan is routed through exactly
ONE target — :func:`resolve_route_mode` resolves which. When
``feishu_interactive`` is sanctioned it OWNS the route (the owner executes
manually and reports the fill; the SimulationExecutor must not auto-fill
the same plan). ``simulation_auto`` (always-on) still backs the broker /
acceptance loop regardless of routing target.

Truthy env values: ``true / 1 / yes / on`` (case-insensitive). Everything
else, including the unset case, is interpreted as ``false`` so the
default boot is the safer simulation-only configuration.

The full account-lifecycle event triggered by a mode switch (archive +
MockBroker reset + Feishu initial reconciliation + freeze window) lives
in the ModeRouter task (D-005); this module is intentionally narrow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger(component="run_mode")

_FEISHU_FLAG_ENV = "FEISHU_INTERACTIVE_ENABLED"
_TRUTHY = frozenset({"true", "1", "yes", "on"})


@dataclass(frozen=True)
class RunMode:
    """Resolved run-mode tuple emitted by :func:`resolve_run_mode`."""

    simulation_auto: bool  # Always True in the current target architecture.
    feishu_interactive: bool


def _read_env_flag(name: str) -> bool:
    """Return True iff the env value normalizes to a truthy token."""
    raw = os.environ.get(name, "").strip().lower()
    return raw in _TRUTHY


def feishu_interactive_enabled() -> bool:
    """Whether the Feishu human-in-loop overlay is configured for this boot."""
    return _read_env_flag(_FEISHU_FLAG_ENV)


def resolve_run_mode() -> RunMode:
    """Resolve the active run mode from environment.

    ``simulation_auto`` is unconditionally True per P0-1 (the baseline is
    always running, the overlay only changes routing target). The flag
    is kept as a struct field so future amendments that decompose the
    baseline can extend the type without renaming call sites.
    """
    return RunMode(
        simulation_auto=True,
        feishu_interactive=feishu_interactive_enabled(),
    )


class RouteMode(StrEnum):
    """The single active outbound routing target for a VALIDATED plan.

    Mutually exclusive by construction — exactly one applies per process,
    so a plan can never be both auto-filled and fanned out (U-B2).
    """

    SIMULATION_AUTO = "simulation_auto"
    """SimulationExecutor auto-fills the MockBroker; no Feishu send."""
    FEISHU_INTERACTIVE = "feishu_interactive"
    """Send to the decision group; owner executes + reports. No auto-fill."""
    DRY_RUN = "dry_run"
    """Render only — no send, no broker mutation (dry-run script / U-D3)."""


def resolve_route_mode(
    run_mode: RunMode | None = None, *, dry_run: bool = False
) -> RouteMode:
    """Resolve the single mutually-exclusive routing target.

    Precedence (fail-safe): ``dry_run`` wins (never touches broker/Feishu);
    else ``feishu_interactive`` owns the route when enabled; else the
    ``simulation_auto`` baseline. The caller passes the resolved
    :class:`RunMode` (from :func:`resolve_run_mode`) plus an explicit
    ``dry_run`` flag — this function reads no env itself so it stays pure
    and unit-testable.
    """
    if dry_run:
        return RouteMode.DRY_RUN
    resolved = run_mode if run_mode is not None else resolve_run_mode()
    if resolved.feishu_interactive:
        return RouteMode.FEISHU_INTERACTIVE
    return RouteMode.SIMULATION_AUTO


def assert_run_mode_env() -> RunMode:
    """Validate the run-mode env at startup and log the resolved tuple.

    Replaces the legacy ``assert_authorization_mode`` call. No ``SystemExit``
    is raised here: the baseline is always valid, and a malformed
    ``FEISHU_INTERACTIVE_ENABLED`` value falls back to ``false`` per the
    truthy-token policy. Feishu credential fail-fast lives in the
    secrets validator (P1-6, task H-001) and gates the actual overlay.
    """
    mode = resolve_run_mode()
    log.info(
        "run_mode_resolved",
        simulation_auto=mode.simulation_auto,
        feishu_interactive=mode.feishu_interactive,
    )
    return mode
