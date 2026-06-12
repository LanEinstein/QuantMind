"""ReviewRecord models — facts-first daily attribution (AA-002).

P1-2.A-amendment-2026-06-12 §1.3: append-only, facts before
conclusions. Every field is a deterministic observation; there is no
free-prose field at all so an LLM has nothing to write into (the locked
4-class LLM write permission set is untouched by construction).

The promotability red line (codex P2-6, anti-hindsight): a
counterfactual that was NOT pre-registered (i.e. did not exist as an
actual HOLD plan / rejected order at decision time) can never carry
``promotable=True`` — the model validator rejects it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

REVIEW_SCHEMA_VERSION = 1
"""Bump on any field-semantics change so AB-era readers can branch."""

ATTRIBUTION_CODE_VERSION = "review.attribution/v1"
"""Version pin of the deterministic attribution maths — recorded on
every DailyReviewRecord so a promotion-evidence consumer can detect
records produced by older derivations."""


class TradeSide(StrEnum):
    """Trade direction of the attributed fill."""

    BUY = "BUY"
    SELL = "SELL"


class VwapQuality(StrEnum):
    """Provenance flag for the day-VWAP comparison basis.

    ``IMPLAUSIBLE`` covers a unit-corrupted kline row (e.g. an amount
    column in 千元 instead of 元 makes VWAP drift 1000x) — the
    comparison is dropped rather than recorded as a wild outlier.
    """

    OK = "ok"
    MISSING = "missing"
    IMPLAUSIBLE = "implausible"


class CounterfactualKind(StrEnum):
    """Provenance class of a counterfactual signal."""

    HOLD_PLAN = "hold_plan"
    REJECTED_ORDER = "rejected_order"
    HYPOTHETICAL = "hypothetical"


class TradeFact(BaseModel):
    """One executed fill with deterministic execution-quality facts."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trade_id: str = Field(min_length=1, max_length=128)
    order_id: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")
    side: TradeSide
    volume: int = Field(gt=0)
    price: float = Field(gt=0.0)
    amount: float = Field(ge=0.0)
    traded_at: datetime

    commission: float = Field(ge=0.0)
    stamp_tax: float = Field(ge=0.0)
    transfer_fee: float = Field(ge=0.0)
    slippage_cost: float = Field(ge=0.0)

    day_vwap: float | None = Field(default=None, gt=0.0)
    execution_vs_vwap_bps: float | None = None
    """Side-adjusted: positive = executed better than the day VWAP
    (BUY below / SELL above). ``None`` whenever ``day_vwap`` is."""
    vwap_quality: VwapQuality = VwapQuality.MISSING

    entry_cost_price: float | None = Field(default=None, gt=0.0)
    """SELL only — the position's entry cost basis when derivable
    (PositionThesis entry_price); ``None`` = unknown, never guessed."""
    holding_return_pct: float | None = None
    """SELL only — (price − entry_cost_price) / entry_cost_price."""

    policy_hash: str | None = Field(default=None, max_length=64)
    style: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _check_vwap_consistency(self) -> TradeFact:
        if self.day_vwap is None and self.execution_vs_vwap_bps is not None:
            raise ValueError(
                "execution_vs_vwap_bps requires day_vwap to be present"
            )
        if self.day_vwap is not None and self.vwap_quality is not (
            VwapQuality.OK
        ):
            raise ValueError("a recorded day_vwap implies vwap_quality=ok")
        if self.holding_return_pct is not None and (
            self.entry_cost_price is None
        ):
            raise ValueError(
                "holding_return_pct requires entry_cost_price"
            )
        return self


class CounterfactualEntry(BaseModel):
    """A 'what the system did NOT do' observation.

    Anti-hindsight red line: ``promotable=True`` requires the signal to
    have been pre-registered (an actual HOLD plan or rejected order
    that existed at decision time). A HYPOTHETICAL entry can never be
    promotable, whatever the caller claims.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    signal_id: str = Field(min_length=1, max_length=128)
    kind: CounterfactualKind
    pre_registered: bool
    promotable: bool = False

    @model_validator(mode="after")
    def _check_promotability(self) -> CounterfactualEntry:
        if self.kind is CounterfactualKind.HYPOTHETICAL:
            if self.pre_registered:
                raise ValueError(
                    "HYPOTHETICAL counterfactuals are by definition not "
                    "pre-registered"
                )
        if self.promotable and not self.pre_registered:
            raise ValueError(
                "promotable=True requires a pre-registered signal "
                "(anti-hindsight red line, codex P2-6)"
            )
        return self


class DailyReviewRecord(BaseModel):
    """One trading day's attribution review — append-only row."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    record_id: UUID = Field(default_factory=uuid4)
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    created_at: datetime
    schema_version: int = REVIEW_SCHEMA_VERSION
    attribution_code_version: str = ATTRIBUTION_CODE_VERSION

    policy_hash: str | None = Field(default=None, max_length=64)
    trade_facts: tuple[TradeFact, ...] = Field(default_factory=tuple)
    counterfactuals: tuple[CounterfactualEntry, ...] = Field(
        default_factory=tuple
    )
    risk_rejected_count: int = Field(ge=0, default=0)
    builder_early_return_count: int = Field(ge=0, default=0)


class ReviewLane(StrEnum):
    """Which non-trading-day cron produced a weekly record (AA-003)."""

    WEEKEND = "weekend"
    HOLIDAY_CATCHUP = "holiday_catchup"


class WeeklyReviewRecord(BaseModel):
    """One ISO week's deep-review aggregate — append-only row (AA-003).

    Purely derived from the week's :class:`DailyReviewRecord` rows; the
    same no-free-prose rule applies (LLM has nothing to write into).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    record_id: UUID = Field(default_factory=uuid4)
    week_key: str = Field(pattern=r"^\d{4}-W\d{2}$")
    window_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    created_at: datetime
    schema_version: int = REVIEW_SCHEMA_VERSION
    lane: ReviewLane
    policy_hash: str | None = Field(default=None, max_length=64)

    expected_trade_dates: tuple[str, ...] = Field(default_factory=tuple)
    reviewed_trade_dates: tuple[str, ...] = Field(default_factory=tuple)
    missing_trade_dates: tuple[str, ...] = Field(default_factory=tuple)

    total_trades: int = Field(ge=0, default=0)
    buy_count: int = Field(ge=0, default=0)
    sell_count: int = Field(ge=0, default=0)
    sell_with_return_count: int = Field(ge=0, default=0)
    sell_win_count: int = Field(ge=0, default=0)
    avg_execution_vs_vwap_bps: float | None = None
    total_fees_cny: float = Field(ge=0.0, default=0.0)
    risk_rejected_total: int = Field(ge=0, default=0)
    builder_early_return_total: int = Field(ge=0, default=0)
    counterfactual_total: int = Field(ge=0, default=0)
    counterfactual_promotable_total: int = Field(ge=0, default=0)


__all__ = [
    "ATTRIBUTION_CODE_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "CounterfactualEntry",
    "CounterfactualKind",
    "DailyReviewRecord",
    "ReviewLane",
    "TradeFact",
    "TradeSide",
    "VwapQuality",
    "WeeklyReviewRecord",
]
