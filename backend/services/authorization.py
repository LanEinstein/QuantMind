"""Authorization mode lifecycle policy (P5A-T03).

Single source of truth for which ``AUTHORIZATION_MODE`` values are
permitted in each ``QUANTMIND_PHASE`` of the master plan. Enforced at:

- ``backend/main.py`` lifespan startup → fail-fast with ``SystemExit``
- ``backend/api/risk.py`` POST ``/api/risk/auth-mode`` → 403 on
  cross-phase escalation attempts

The canonical ledger (short form) lines up with the master plan §2.9
red lines:

- ``phase5_eval`` / ``phase6_prep``: only ``"suggest"``
- ``phase6_dryrun``: ``"suggest"`` | ``"confirm"``
- ``phase7_live``: any of the three

Legacy long-form labels (``"suggestion"`` / ``"semi_auto"`` /
``"full_auto"``) are recognized for backward compatibility with the
risk API and map 1-to-1 onto the canonical short forms before the
phase check runs.
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(component="authorization")


# Canonical short-form vocabulary used internally and in the master
# plan. Anything outside this set after normalization is rejected.
CANONICAL_MODES: frozenset[str] = frozenset({"suggest", "confirm", "auto"})

# Translate the legacy risk-API long forms onto the canonical short
# vocabulary. The reverse direction is intentionally not provided —
# we want callers to write the canonical form going forward.
_LONG_TO_SHORT: dict[str, str] = {
    "suggestion": "suggest",
    "semi_auto": "confirm",
    "full_auto": "auto",
}

ALLOWED_MODES_BY_PHASE: dict[str, frozenset[str]] = {
    "phase5_eval": frozenset({"suggest"}),
    "phase6_prep": frozenset({"suggest"}),
    "phase6_dryrun": frozenset({"suggest", "confirm"}),
    "phase7_live": frozenset({"suggest", "confirm", "auto"}),
}

_DEFAULT_PHASE = "phase5_eval"
_DEFAULT_MODE = "suggest"


class CrossPhaseAuthorizationError(PermissionError):
    """Raised when a mode change is not permitted in the active phase.

    Subclass of ``PermissionError`` so handlers that catch the standard
    exception still see it without coupling to this module.
    """


def normalize_mode(raw: str) -> str:
    """Canonicalize an authorization-mode label.

    Accepts both the short form (``suggest``/``confirm``/``auto``) and
    the legacy long form (``suggestion``/``semi_auto``/``full_auto``).
    Unknown values pass through so the caller can decide whether to
    reject — that keeps the helper composable with format validators
    that may raise their own errors.
    """
    lowered = raw.strip().lower()
    return _LONG_TO_SHORT.get(lowered, lowered)


def current_phase() -> str:
    """Read the configured phase, falling back to the safe default."""
    return os.environ.get("QUANTMIND_PHASE", _DEFAULT_PHASE).strip().lower()


def current_mode() -> str:
    """Read AUTHORIZATION_MODE in canonical short form."""
    return normalize_mode(
        os.environ.get("AUTHORIZATION_MODE", _DEFAULT_MODE)
    )


def assert_authorization_mode() -> tuple[str, str]:
    """Verify the configured phase + mode pair, or refuse to start.

    Called once during ``backend/main.py`` lifespan startup. Raises
    ``SystemExit`` (which uvicorn converts into a non-zero exit code)
    when:

    - ``QUANTMIND_PHASE`` is unknown — likely a typo or a future-phase
      env file shipping early.
    - ``AUTHORIZATION_MODE`` does not normalize to a canonical mode.
    - The configured mode is not in the phase's allow-list.

    Returns the resolved ``(phase, canonical_mode)`` tuple on success
    for the caller to log.
    """
    phase = current_phase()
    mode = current_mode()

    allowed = ALLOWED_MODES_BY_PHASE.get(phase)
    if allowed is None:
        raise SystemExit(
            f"Refusing to start: unknown QUANTMIND_PHASE={phase!r} "
            f"(known: {sorted(ALLOWED_MODES_BY_PHASE)})"
        )

    if mode not in CANONICAL_MODES:
        raise SystemExit(
            f"Refusing to start: invalid AUTHORIZATION_MODE={mode!r} "
            f"(known: {sorted(CANONICAL_MODES)} or legacy "
            f"{sorted(_LONG_TO_SHORT)})"
        )

    if mode not in allowed:
        raise SystemExit(
            f"Refusing to start: AUTHORIZATION_MODE={mode!r} "
            f"not allowed in phase {phase!r} (allowed: {sorted(allowed)})"
        )

    log.info("authorization_assertion_passed", phase=phase, mode=mode)
    return phase, mode


def assert_mode_allowed_for_phase(
    new_mode: str, *, phase: str | None = None
) -> str:
    """Validate a mode change request against the phase's allow-list.

    Raises :class:`CrossPhaseAuthorizationError` if the new mode is not
    permitted in the active phase. Returns the canonical short form on
    success so the caller can write it back to the environment without
    re-normalizing.
    """
    canonical = normalize_mode(new_mode)
    if canonical not in CANONICAL_MODES:
        raise CrossPhaseAuthorizationError(
            f"Mode {new_mode!r} is not a recognized authorization mode "
            f"(allowed: {sorted(CANONICAL_MODES)} or legacy "
            f"{sorted(_LONG_TO_SHORT)})"
        )
    active_phase = phase or current_phase()
    allowed = ALLOWED_MODES_BY_PHASE.get(active_phase)
    if allowed is None:
        raise CrossPhaseAuthorizationError(
            f"Unknown phase {active_phase!r}; refusing mode change"
        )
    if canonical not in allowed:
        raise CrossPhaseAuthorizationError(
            f"Mode {new_mode!r} (canonical {canonical!r}) is not "
            f"permitted in phase {active_phase!r} "
            f"(allowed: {sorted(allowed)})"
        )
    return canonical
