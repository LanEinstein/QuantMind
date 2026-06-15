"""RuntimeParamStore — boot-time landing point for evolved quant params
(AE-006 / AB-003-amendment-2026-06-14 §2.2).

The last segment of the quantitative-parameter evolution loop
(discover → pre-screen → frozen forward-shadow → human pin → **restart takes
effect**). The lockfile schema v2 ``params`` block is pinned by the human
gate; this store is the single, IMMUTABLE runtime read point for it:

* :meth:`from_lockfile` loads ``params`` from ``config/live_artifacts.lock.json``
  AFTER :func:`~backend.strategy_evolution.activation.apply_pending_activation`
  has swapped in any staged manifest, then re-validates the WHOLE set through
  :func:`validate_param_set_for_activation` (whitelist + immutable clamp +
  group constraints + frozen-baseline monotonicity). Any violation fails the
  boot path closed — a silent acceptance of an out-of-clamp param is worse
  than refusing to start.
* A v1 lockfile (or a v2 lockfile with no ``params``) yields an EMPTY store.
  Every consumer reads through :meth:`get` with its frozen code default, so an
  empty store is byte-identical to the pre-AE-006 system (§4 red line 1).
* The store is fully immutable (``__setattr__`` raises; no mutate/reload). A
  param only changes via amendment + human pin + restart — ``config`` is
  runtime-immutable + hot-reload-forbidden, unchanged by this amendment.

Module isolation (inherits P2-2): no ``backend.{api,broker,risk,llm,agents,
mirofish,data}`` imports — this lives entirely inside the evolution package and
the deterministic config layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import structlog

from backend.strategy_evolution.evolvable_params import (
    FrozenParamViolationError,
    validate_param_set_for_activation,
)
from backend.strategy_evolution.live_artifact_registry import (
    load_lockfile,
)

log = structlog.get_logger(component="strategy_evolution.runtime_param_store")


class RuntimeParamStoreError(Exception):
    """Base class for store-load failures; subclasses fail-close the boot."""


class RuntimeParamValidationError(RuntimeParamStoreError):
    """The pinned ``params`` block failed whitelist / clamp / group / frozen
    baseline re-validation (the boot defence-in-depth gate)."""


class RuntimeParamStore:
    """Immutable, validated snapshot of the pinned evolved params.

    Build via :meth:`from_lockfile` (production) or :meth:`from_params`
    (in-memory / tests). Both re-validate; both produce a frozen snapshot
    with no mutate/reload surface.
    """

    __slots__ = ("_params",)
    _params: Mapping[str, float]

    def __init__(self, params: Mapping[str, float]) -> None:
        # Wrap in a MappingProxyType so a leaked reference cannot mutate the
        # snapshot (mirrors LiveArtifactRegistry — the store's whole point is
        # amendment + restart-only param changes).
        object.__setattr__(
            self, "_params", MappingProxyType(dict(params))
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"RuntimeParamStore is immutable; cannot set {name!r}. There is no "
            f"runtime path to change a param — promotions require amendment + "
            f"human pin + restart (AB-003-amendment-2026-06-14)."
        )

    # -- read accessors -----------------------------------------------------

    def get(self, name: str, default: float) -> float:
        """Return the pinned value for ``name`` or ``default`` when unset.

        This is the ONLY consumer-facing accessor: a construction site reads
        ``store.get("selector.weight_momentum", CODE_DEFAULT)`` so an empty
        store (the common case) is byte-identical to the hard-coded default.
        """
        return self._params.get(name, default)

    def has(self, name: str) -> bool:
        """True iff ``name`` carries a pinned (overriding) value."""
        return name in self._params

    def as_mapping(self) -> Mapping[str, float]:
        """Read-only view of the full pinned set (callers cannot mutate it)."""
        return self._params

    def __len__(self) -> int:
        return len(self._params)

    def __bool__(self) -> bool:
        return bool(self._params)

    # -- builders -----------------------------------------------------------

    @classmethod
    def from_params(
        cls, params: Mapping[str, float]
    ) -> RuntimeParamStore:
        """Build + re-validate from an in-memory param map (tests / boot).

        Raises:
            RuntimeParamValidationError: any name / clamp / group /
                frozen-baseline / no-wired-consumer violation (fail-closed —
                never silently drop a bad param, never carry a param that no
                runtime consumer reads).
        """
        if params:
            try:
                validation = validate_param_set_for_activation(dict(params))
            except FrozenParamViolationError as exc:
                raise RuntimeParamValidationError(
                    f"pinned params touch the frozen non-evolvable set: {exc}"
                ) from exc
            if not validation.passed:
                raise RuntimeParamValidationError(
                    "pinned params failed activation re-validation: "
                    + "; ".join(validation.violations)
                )
        return cls(params)

    @classmethod
    def from_lockfile(cls, lock_path: Path | str) -> RuntimeParamStore:
        """Load + re-validate the ``params`` block from the live lockfile.

        Uses the shared :func:`load_lockfile` (the same fail-closed locate +
        parse + error taxonomy as :class:`LiveArtifactRegistry`) so the params
        block is parsed by the identical strict model that gates approved
        hashes, and the two loaders never drift. A missing file / malformed
        schema fails closed; an empty / v1 lockfile yields an empty store
        (byte-identical runtime).
        """
        lock = load_lockfile(lock_path)
        store = cls.from_params(lock.params)
        if store:
            log.info(
                "runtime_param_store_loaded",
                count=len(store),
                names=sorted(store.as_mapping()),
            )
        return store


__all__ = [
    "RuntimeParamStore",
    "RuntimeParamStoreError",
    "RuntimeParamValidationError",
]
