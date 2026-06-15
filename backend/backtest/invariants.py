"""Closed-form backtest invariants (AE-004 §2.4, codex J3).

Two engines can agree on a *shared* bug (the N=2 common-mode blind spot): the
strategy's sizing and the broker's acceptance could both be wrong the same way.
These invariants are an independent, **framework-version-independent** algebraic
check — they re-derive the books from first principles and refuse to depend on
either engine's internals:

1. **Cash conservation** — ``initial + Σ sell_net − Σ buy_net == final`` (integer
   分, zero tolerance).
2. **Position conservation** — per code, ``opening + Σ buy_vol − Σ sell_vol``
   equals the final holding and is never negative.
3. **Fee recompute** — every fill's recorded friction equals the friction the
   formula re-derives from its fill price + volume + board (the books cannot
   have charged a different fee than the model says).
4. **Exposure caps** — at each BUY the post-fill single-stock exposure ≤ 15% and
   total-position exposure ≤ 70% of total equity (the live RiskEngine bound,
   re-verified). Checked **at the buy**, not at every mark-to-market point:
   price appreciation legitimately drifts a held position above its entry cap;
   the cap is a sizing constraint at order time, so a per-MTM-point assertion
   would false-positive on a winning position. The harness records the
   post-fill valuation context (:class:`ExposureObservation`); the cap test is
   pure-integer (``value * 100 ≤ equity * cap_percent``) so no float / numpy
   version can perturb it.

Any violation makes the run :attr:`InvariantVerdict.DIVERGENT`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from backend.backtest.friction import FrictionParams, compute_fill_economics
from backend.backtest.portfolio import AppliedFill, OpeningLot

DEFAULT_SINGLE_STOCK_CAP_PERCENT = 15
"""单股 ≤15% of total equity (RiskConfig position triad)."""

DEFAULT_TOTAL_POSITION_CAP_PERCENT = 70
"""总仓 ≤70% of total equity (RiskConfig position triad)."""

_PERCENT_DENOM = 100
"""Integer percent base — keeps the cap test free of float literals."""


class InvariantVerdict(StrEnum):
    CONSISTENT = "consistent"
    DIVERGENT = "divergent"


@dataclass(frozen=True)
class InvariantViolation:
    """One broken invariant (immutable)."""

    kind: str
    detail: str


@dataclass(frozen=True)
class InvariantReport:
    """Outcome of the closed-form checks (immutable)."""

    verdict: InvariantVerdict
    violations: tuple[InvariantViolation, ...]

    @property
    def consistent(self) -> bool:
        return self.verdict is InvariantVerdict.CONSISTENT


@dataclass(frozen=True)
class ExposureObservation:
    """Post-BUY-fill valuation context recorded by the loop (integer 分)."""

    trade_date: str
    code: str
    position_value_cents: int
    total_holdings_value_cents: int
    total_equity_cents: int


def _check_cash_conservation(
    *,
    initial_cash_cents: int,
    fills: Sequence[AppliedFill],
    final_cash_cents: int,
) -> InvariantViolation | None:
    delta = 0
    for fill in fills:
        if fill.side_is_buy:
            delta -= fill.net_cents
        else:
            delta += fill.net_cents
    expected = initial_cash_cents + delta
    if expected != final_cash_cents:
        return InvariantViolation(
            kind="cash_conservation",
            detail=(
                f"initial {initial_cash_cents} + Δ {delta} = {expected} "
                f"!= final {final_cash_cents}"
            ),
        )
    return None


def _check_position_conservation(
    *,
    opening_positions: Sequence[OpeningLot],
    fills: Sequence[AppliedFill],
    final_positions: Sequence[tuple[str, int]],
) -> list[InvariantViolation]:
    net: dict[str, int] = {}
    for lot in opening_positions:
        net[lot.code] = net.get(lot.code, 0) + lot.volume
    for fill in fills:
        step = fill.volume if fill.side_is_buy else -fill.volume
        net[fill.code] = net.get(fill.code, 0) + step
    violations: list[InvariantViolation] = []
    for code, volume in sorted(net.items()):
        if volume < 0:
            violations.append(
                InvariantViolation(
                    kind="position_conservation",
                    detail=f"{code}: net volume {volume} < 0 (over-sold)",
                )
            )
    recorded = {code: vol for code, vol in final_positions}
    expected = {code: vol for code, vol in net.items() if vol > 0}
    if recorded != expected:
        violations.append(
            InvariantViolation(
                kind="position_conservation",
                detail=f"final holdings {recorded} != recomputed {expected}",
            )
        )
    return violations


def _check_fee_recompute(
    *, fills: Sequence[AppliedFill], params: FrictionParams
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    for fill in fills:
        # The fill price already embeds slippage (board model or harsh impact),
        # so recompute with apply_board_slippage=False — the fee-only check.
        econ = compute_fill_economics(
            side_is_buy=fill.side_is_buy,
            order_price_cents=fill.fill_price_cents,
            volume=fill.volume,
            board=fill.board,
            transfer_fee_applies=fill.transfer_fee_applies,
            params=params,
            apply_board_slippage=False,
        )
        mismatches = [
            f"{name} recorded {recorded} != recomputed {derived}"
            for name, recorded, derived in (
                ("commission", fill.commission_cents, econ.commission_cents),
                ("stamp_tax", fill.stamp_tax_cents, econ.stamp_tax_cents),
                (
                    "transfer_fee",
                    fill.transfer_fee_cents,
                    econ.transfer_fee_cents,
                ),
                ("net", fill.net_cents, econ.net_cents),
            )
            if recorded != derived
        ]
        if mismatches:
            violations.append(
                InvariantViolation(
                    kind="fee_recompute",
                    detail=f"{fill.trade_date} {fill.code}: " + "; ".join(mismatches),
                )
            )
    return violations


def _check_exposure_caps(
    *,
    exposures: Sequence[ExposureObservation],
    single_stock_cap_percent: int,
    total_position_cap_percent: int,
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    for obs in exposures:
        equity = obs.total_equity_cents
        # value * 100 <= equity * cap_percent — pure-integer, version-proof.
        single_lhs = obs.position_value_cents * _PERCENT_DENOM
        if single_lhs > equity * single_stock_cap_percent:
            violations.append(
                InvariantViolation(
                    kind="single_stock_cap",
                    detail=(
                        f"{obs.trade_date} {obs.code}: position "
                        f"{obs.position_value_cents} > {single_stock_cap_percent}% "
                        f"of equity {equity}"
                    ),
                )
            )
        if (
            obs.total_holdings_value_cents * _PERCENT_DENOM
            > equity * total_position_cap_percent
        ):
            violations.append(
                InvariantViolation(
                    kind="total_position_cap",
                    detail=(
                        f"{obs.trade_date} {obs.code}: total holdings "
                        f"{obs.total_holdings_value_cents} > "
                        f"{total_position_cap_percent}% of equity {equity}"
                    ),
                )
            )
    return violations


def check_invariants(
    *,
    initial_cash_cents: int,
    fills: Sequence[AppliedFill],
    final_cash_cents: int,
    opening_positions: Sequence[OpeningLot] = (),
    final_positions: Sequence[tuple[str, int]] = (),
    params: FrictionParams,
    exposures: Sequence[ExposureObservation] = (),
    single_stock_cap_percent: int = DEFAULT_SINGLE_STOCK_CAP_PERCENT,
    total_position_cap_percent: int = DEFAULT_TOTAL_POSITION_CAP_PERCENT,
) -> InvariantReport:
    """Run every closed-form check; DIVERGENT on any violation (pure)."""
    violations: list[InvariantViolation] = []
    cash = _check_cash_conservation(
        initial_cash_cents=initial_cash_cents,
        fills=fills,
        final_cash_cents=final_cash_cents,
    )
    if cash is not None:
        violations.append(cash)
    violations += _check_position_conservation(
        opening_positions=opening_positions,
        fills=fills,
        final_positions=final_positions,
    )
    violations += _check_fee_recompute(fills=fills, params=params)
    violations += _check_exposure_caps(
        exposures=exposures,
        single_stock_cap_percent=single_stock_cap_percent,
        total_position_cap_percent=total_position_cap_percent,
    )
    verdict = (
        InvariantVerdict.CONSISTENT if not violations else InvariantVerdict.DIVERGENT
    )
    return InvariantReport(verdict=verdict, violations=tuple(violations))


__all__ = [
    "DEFAULT_SINGLE_STOCK_CAP_PERCENT",
    "DEFAULT_TOTAL_POSITION_CAP_PERCENT",
    "ExposureObservation",
    "InvariantReport",
    "InvariantViolation",
    "InvariantVerdict",
    "check_invariants",
]
