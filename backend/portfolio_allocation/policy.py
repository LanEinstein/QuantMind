"""Allocation policy config + loader (Phase P P-002).

A frozen, runtime-immutable policy object that bundles the
``config/allocation_policy.yaml`` knobs with the three position limits read
from ``config/risk.yaml`` (single source of truth — the 15% / ¥50k / 100-lot
constants are **never** duplicated in the allocation YAML, mirroring
``backend/budget_policy/policy.py``). The policy exposes thin convenience
methods that delegate to the pure :mod:`backend.portfolio_allocation.allocator`
and :mod:`backend.portfolio_allocation.volatility` functions, so the Line-1
provider (P-003) maps policy knobs to function kwargs in exactly one place.

Validation is strict + fail-fast: ``deploy_fraction ∈ (0, 1]``,
``per_name_target_pct ∈ (0, single_stock_cap]`` (the per-name target must be
*at or below* the 15% hard cap — the allocation layer is strictly more
conservative), ``cash_buffer_pct ∈ [0, 1)``, ``vol_lookback ≥ 2``. Load-once,
no hot-reload (P0-7 §2 / amendment §2.5). No
``import backend.{llm,agents,mirofish}`` (redline ``[P-002]``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

from backend.portfolio_allocation.allocator import (
    cash_to_lots,
    compute_target_cash,
    deployable_cash,
)
from backend.portfolio_allocation.volatility import inverse_vol_weights

log = structlog.get_logger(component="portfolio_allocation")

# The only supported weighting method (equal-weight is the built-in fallback,
# not a separately selectable method — see volatility.inverse_vol_weights).
INVERSE_VOLATILITY: str = "inverse_volatility"
_SUPPORTED_METHODS: frozenset[str] = frozenset({INVERSE_VOLATILITY})

__all__ = [
    "INVERSE_VOLATILITY",
    "AllocationPolicy",
    "AllocationPolicyError",
    "load_allocation_policy",
]


class AllocationPolicyError(ValueError):
    """Raised when the allocation / risk config fails validation."""


@dataclass(frozen=True)
class AllocationPolicy:
    """Locked allocation knobs + single-source position limits (runtime-immutable).

    ``single_stock_cap_pct`` / ``single_instruction_cap`` / ``lot_size`` are
    read from ``config/risk.yaml`` ``position_limits`` by
    :func:`load_allocation_policy`, never duplicated in the allocation YAML.

    ``vol_lookback`` documents the σ window the weighting **expects**; σ is
    computed upstream (the screener's ``volatility_20d``) and passed in, so
    :meth:`inverse_vol_weights` does not re-window it — the knob is consumed
    where σ is sourced (the Line-1 provider, P-003), to assert alignment.
    """

    method: str
    deploy_fraction: float
    per_name_target_pct: float
    cash_buffer_pct: float
    vol_lookback: int
    single_stock_cap_pct: float
    single_instruction_cap: float
    lot_size: int

    def inverse_vol_weights(
        self, sigma_by_code: dict[str, float | None]
    ) -> dict[str, float]:
        """Deterministic inverse-volatility weights (equal-weight fallback)."""
        return inverse_vol_weights(sigma_by_code)

    def deployable_cash(self, available_cash: float, total_assets: float) -> float:
        """Today's deployable cash envelope under this policy's tranching knobs."""
        return deployable_cash(
            available_cash,
            total_assets,
            deploy_fraction=self.deploy_fraction,
            cash_buffer_pct=self.cash_buffer_pct,
        )

    def target_cash(
        self,
        weights: dict[str, float],
        deployable: float,
        total_assets: float,
        existing_value_by_code: dict[str, float],
    ) -> dict[str, float]:
        """Per-name incremental cash target, clamped to this policy's caps."""
        return compute_target_cash(
            weights,
            deployable,
            total_assets,
            existing_value_by_code,
            per_name_target_pct=self.per_name_target_pct,
            single_stock_cap_pct=self.single_stock_cap_pct,
            single_instruction_cap=self.single_instruction_cap,
        )

    def cash_to_lots(self, target_cash: float, price: float) -> int:
        """Whole-lot share count for ``target_cash`` at ``price`` (0 = skip today)."""
        return cash_to_lots(target_cash, price, lot=self.lot_size)


def load_allocation_policy(
    allocation_yaml_path: str | Path, risk_yaml_path: str | Path
) -> AllocationPolicy:
    """Load + validate the allocation policy (runtime-immutable, load-once).

    Reads the ``allocation`` block from ``allocation_yaml_path`` and the
    ``position_limits`` block from ``risk_yaml_path`` (single source for the
    15% / ¥50k / 100-lot constants — never duplicated).

    Raises:
        FileNotFoundError: either file does not exist.
        AllocationPolicyError: any knob / limit invariant is violated.
    """
    alloc = _load_block(allocation_yaml_path, "allocation")
    limits = _load_block(risk_yaml_path, "position_limits")

    single_stock_cap_pct = _require_unit_fraction(
        limits, "max_single_stock_pct", "position_limits", upper_inclusive=True
    )
    single_instruction_cap = _require_positive_float(
        limits, "max_single_instruction_amount", "position_limits"
    )
    lot_size = _require_positive_int(limits, "volume_lot_size", "position_limits")

    method = alloc.get("method")
    if method not in _SUPPORTED_METHODS:
        raise AllocationPolicyError(
            f"allocation.method must be one of {sorted(_SUPPORTED_METHODS)}, "
            f"got {method!r}"
        )

    deploy_fraction = _require_unit_fraction(
        alloc, "deploy_fraction", "allocation", upper_inclusive=True
    )
    cash_buffer_pct = _require_unit_fraction(
        alloc, "cash_buffer_pct", "allocation", upper_inclusive=False
    )
    per_name_target_pct = _require_positive_float(
        alloc, "per_name_target_pct", "allocation"
    )
    if per_name_target_pct > single_stock_cap_pct:
        raise AllocationPolicyError(
            f"allocation.per_name_target_pct ({per_name_target_pct}) must be "
            f"<= position_limits.max_single_stock_pct ({single_stock_cap_pct}) "
            "— the per-name target can only be tighter than the 15% hard cap"
        )
    vol_lookback = _require_positive_int(alloc, "vol_lookback", "allocation")
    if vol_lookback < 2:
        raise AllocationPolicyError(
            f"allocation.vol_lookback must be >= 2, got {vol_lookback}"
        )

    policy = AllocationPolicy(
        method=method,
        deploy_fraction=deploy_fraction,
        per_name_target_pct=per_name_target_pct,
        cash_buffer_pct=cash_buffer_pct,
        vol_lookback=vol_lookback,
        single_stock_cap_pct=single_stock_cap_pct,
        single_instruction_cap=single_instruction_cap,
        lot_size=lot_size,
    )
    log.info(
        "allocation_policy_loaded",
        method=method,
        deploy_fraction=deploy_fraction,
        per_name_target_pct=per_name_target_pct,
        cash_buffer_pct=cash_buffer_pct,
        vol_lookback=vol_lookback,
        single_stock_cap_pct=single_stock_cap_pct,
        single_instruction_cap=single_instruction_cap,
        lot_size=lot_size,
    )
    return policy


def _load_block(yaml_path: str | Path, block: str) -> dict[str, Any]:
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    section = raw.get(block)
    if not isinstance(section, dict):
        raise AllocationPolicyError(f"{path.name} missing '{block}' section")
    return section


def _require_positive_float(block: dict[str, Any], key: str, ns: str) -> float:
    value = block.get(key)
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or float(value) <= 0
    ):
        raise AllocationPolicyError(
            f"{ns}.{key} must be a positive number, got {value!r}"
        )
    return float(value)


def _require_positive_int(block: dict[str, Any], key: str, ns: str) -> int:
    value = block.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AllocationPolicyError(f"{ns}.{key} must be a positive int, got {value!r}")
    return value


def _require_unit_fraction(
    block: dict[str, Any], key: str, ns: str, *, upper_inclusive: bool
) -> float:
    value = block.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise AllocationPolicyError(f"{ns}.{key} must be a number, got {value!r}")
    f = float(value)
    ok = (0.0 < f <= 1.0) if upper_inclusive else (0.0 <= f < 1.0)
    if not ok:
        bound = "(0, 1]" if upper_inclusive else "[0, 1)"
        raise AllocationPolicyError(f"{ns}.{key} must be in {bound}, got {f}")
    return f
