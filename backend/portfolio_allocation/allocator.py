"""Robust-tranche cash allocation + whole-lot sizing (Phase P P-002).

Deterministic, pure-stdlib helpers that turn inverse-volatility weights
into a per-name **incremental cash target** and then into a whole-lot
share count, so the Line-1 provider can clamp each BUY with
``min(max_compliant_buy_volume(...), target)`` (P0-7-amendment-2026-05-30
§2.2 — allocation **only tightens, never relaxes** the 15% / ¥50k / cash
/ ≤5-order rules; RiskEngine stays independently authoritative).

Conservative tranching (§2.1): only a fraction of available cash is
deployed per day (``deploy_fraction``), each name is capped to a target
weight (``per_name_target_pct`` < the 15% hard cap), and a cash buffer is
kept. Allocation is *incremental* — existing holdings of a code consume
its single-stock headroom (long-only + T+1, no same-day recycling
assumed). Every function is deterministic: identical inputs → identical
output (R0 §2.0 PIT replay).

No ``import backend.{llm,agents,mirofish}`` (redline ``[P-002]``).
"""

from __future__ import annotations

import math

__all__ = ["cash_to_lots", "compute_target_cash", "deployable_cash"]


def deployable_cash(
    available_cash: float,
    total_assets: float,
    *,
    deploy_fraction: float,
    cash_buffer_pct: float,
) -> float:
    """Today's deployable cash envelope (conservative tranching).

    ``max(0, min(available_cash × deploy_fraction,
    available_cash − cash_buffer_pct × total_assets))`` — never deploys more
    than a fraction of cash, and always keeps a buffer of total assets.
    Non-finite inputs fail closed to ``0.0``.
    """
    if not math.isfinite(available_cash) or not math.isfinite(total_assets):
        return 0.0
    by_fraction = available_cash * deploy_fraction
    by_buffer = available_cash - cash_buffer_pct * total_assets
    return max(0.0, min(by_fraction, by_buffer))


def compute_target_cash(
    weights: dict[str, float],
    deployable: float,
    total_assets: float,
    existing_value_by_code: dict[str, float],
    *,
    per_name_target_pct: float,
    single_stock_cap_pct: float,
    single_instruction_cap: float,
    eps: float = 1e-9,
) -> dict[str, float]:
    """Per-name incremental cash target (¥), each ``≥ 0`` and ``≤`` its cap.

    Steps (§2.1):
      1. ``raw[c] = weights[c] × deployable``.
      2. ``cap[c] = max(0, min(per_name_target_pct × total_assets,
         single_stock_cap_pct × total_assets − existing_value[c],
         single_instruction_cap))`` — the 15% headroom is *incremental*
         (existing holdings consume it); the ¥50k single-instruction cap is
         the single source from ``config/risk.yaml``.
      3. ``alloc[c] = min(raw[c], cap[c])``.
      4. One-pass residual redistribution: spread any
         ``deployable − Σalloc`` over not-yet-capped names proportional to
         their weights, re-clamped to ``cap[c]``.

    Guarantees (adversarial invariant): for *any* weights — including
    non-finite, negative, or summing to more than 1 — every
    ``alloc[c] ≤ cap[c]`` and ``Σalloc ≤ deployable`` (up to floating-point
    rounding); allocation never relaxes a cap. Deterministic.
    """
    if not weights:
        return {}
    if not math.isfinite(deployable) or deployable <= eps:
        return {code: 0.0 for code in weights}

    # Sanitize weights (fail-closed on corrupt input): non-finite / non-positive
    # weights drop to 0, and a weight vector summing to more than 1 is
    # renormalized so ``Σtarget ≤ deployable`` holds for *any* caller input —
    # not only inverse_vol_weights' already-normalized output. For a normalized
    # vector this is a no-op (a sum of 1 + a float ULP is also clamped down).
    clean = {
        code: (w if math.isfinite(w) and w > 0.0 else 0.0)
        for code, w in weights.items()
    }
    weight_total = sum(clean.values())
    if weight_total > 1.0:
        clean = {code: w / weight_total for code, w in clean.items()}

    cap: dict[str, float] = {}
    for code in weights:
        existing = existing_value_by_code.get(code, 0.0)
        if not math.isfinite(existing):
            existing = math.inf  # corrupt existing value → zero headroom
        headroom = single_stock_cap_pct * total_assets - existing
        cap[code] = max(
            0.0,
            min(per_name_target_pct * total_assets, headroom, single_instruction_cap),
        )

    alloc: dict[str, float] = {
        code: min(clean[code] * deployable, cap[code]) for code in weights
    }

    # One-pass residual redistribution over not-yet-capped names. Single-pass is
    # intentional (redline 6: no mid-walk dynamic reallocation) — cash a
    # re-capped name cannot absorb is left under-deployed (conservative), never
    # pushed past deployable.
    residual = deployable - sum(alloc.values())
    if residual > eps:
        uncapped = [c for c in weights if alloc[c] < cap[c] - eps]
        weight_sum = sum(clean[c] for c in uncapped)
        if weight_sum > eps:
            for code in uncapped:
                extra = residual * (clean[code] / weight_sum)
                alloc[code] = min(cap[code], alloc[code] + extra)
    return alloc


def cash_to_lots(target_cash: float, price: float, lot: int = 100) -> int:
    """Whole-lot share count affordable for ``target_cash`` at ``price``.

    Returns ``floor(target_cash / (price × lot)) × lot`` shares (a multiple
    of ``lot``). A non-positive / non-finite price or target, or a
    non-positive lot, yields ``0`` — and **0 means "do not buy this name
    today"** (the caller must treat 0 as a skip, never coerce it to 1 lot,
    which would violate the ``volume > 0`` schema). Deterministic.
    """
    if lot <= 0 or not math.isfinite(price) or price <= 0:
        return 0
    if not math.isfinite(target_cash) or target_cash <= 0:
        return 0
    lots = math.floor(target_cash / (price * lot))
    return max(0, lots) * lot
