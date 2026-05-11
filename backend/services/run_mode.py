"""Run-mode interpretation layer (P0-1).

This module is the single source of truth for whether the running process
is in plain ``simulation_auto`` (always-on baseline) or whether the
``feishu_interactive`` overlay is also active. It replaces the legacy
``AUTHORIZATION_MODE`` × ``QUANTMIND_PHASE`` matrix that was tied to a
real-broker live path (see ``docs/decisions/project_run_mode_p0_1.md``).

Behavior summary:
- ``simulation_auto`` is always running — it backs MockBroker + the
  acceptance loop and is the only mode that needs zero external auth.
- ``FEISHU_INTERACTIVE_ENABLED=true`` overlays the Feishu human-in-loop
  routing target. The 45-trading-day acceptance gate guards the actual
  switch; this module only reads env and reports what is configured.

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
