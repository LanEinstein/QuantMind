"""Deterministic bottom-confirmation + anti-chase gate (AF-004).

A code is a *confirmed bottom* (value-埋伏 eligible) only when EVERY check below
holds (fail-closed: any missing input ⇒ not confirmed):

* **站稳筹码成本带 (above_cost)** — last close ≥ the chip-distribution median
  cost (``cyq_perf.cost_50pct``) net of a small tolerance: holders are roughly
  whole, the knife has stopped falling.
* **不远高于成本 (not_overextended)** — last close ≤ median cost × (1 + premium):
  bought near holder cost, not extended above it.
* **离 52 周高有距离 (below_recent_high)** — last close ≤ ``his_high`` × (1 −
  proximity): not chasing into the prior high.
* **近期未大涨 (no_runup)** — the trailing ``long_window`` return ≤ a cap: not
  already run up (提前埋伏, not chase).
* **缩量 (volume_shrinking)** — the recent ``short_window`` mean turnover ≤
  ``max_volume_shrink_ratio`` × the ``long_window`` mean: selling has dried up.
* **无破位 / 企稳 (no_breakdown)** — last close is ≥ ``stabilise_margin`` above
  the ``breakdown_window`` low: price lifted off the bottom, no fresh low.
* **筹码非亢奋 (winner_rate_ok)** — ``cyq_perf.winner_rate`` ≤ a cap: not the
  euphoric top where everyone is already in profit.

Pure, deterministic, 0 LLM. The trailing price/turnover window is supplied by the
caller (the same PIT frame the screener consumes); the chip snapshot is read from
the :class:`SnapshotStore` (``cyq_perf``, PIT by trade date). Replays bit-exact.
"""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

VENDOR = "tushare"
ENDPOINT_CYQ = "cyq_perf"


@dataclass(frozen=True)
class BottomConfirmationConfig:
    """Runtime-immutable bottom-confirmation thresholds (amendment-gated).

    Reasoned defaults; the empirical symbol/threshold calibration is the QGR
    slow-leg's job (shared 思路). A live change is an offline recalibration
    (P2-2 evolution whitelist + shadow + human gate), never a hot-reload.
    """

    short_window: int = 5
    long_window: int = 20
    max_volume_shrink_ratio: float = 0.85
    breakdown_window: int = 20
    stabilise_margin: float = 0.02
    cost_band_floor_tol: float = 0.05
    cost_premium_max: float = 0.30
    high_52w_proximity: float = 0.10
    max_runup_pct: float = 0.30
    winner_rate_max: float = 85.0

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < self.short_window:
            raise ValueError("require 1 ≤ short_window ≤ long_window")
        if self.breakdown_window < 1:
            raise ValueError("breakdown_window must be ≥ 1")
        for name in (
            "max_volume_shrink_ratio",
            "stabilise_margin",
            "cost_band_floor_tol",
            "cost_premium_max",
            "high_52w_proximity",
            "max_runup_pct",
        ):
            v = getattr(self, name)
            if (
                not isinstance(v, int | float)
                or isinstance(v, bool)
                or not math.isfinite(v)
                or v < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if not math.isfinite(self.winner_rate_max) or not (
            0.0 <= self.winner_rate_max <= 100.0
        ):
            raise ValueError("winner_rate_max must be a finite value in [0, 100]")


@dataclass(frozen=True)
class PriceWindow:
    """Trailing PIT close + turnover series for one code (oldest → newest)."""

    closes: tuple[float, ...]
    amounts: tuple[float, ...]  # ¥ turnover (daily.amount), aligned to closes


@dataclass(frozen=True)
class ChipCost:
    """The PIT chip-distribution facts the bottom gate consumes (cyq_perf)."""

    cost_50pct: float
    winner_rate: float
    his_high: float


@dataclass(frozen=True)
class BottomSignals:
    """Per-check verdict + the overall confirmation (display + audit)."""

    above_cost: bool
    not_overextended: bool
    below_recent_high: bool
    no_runup: bool
    volume_shrinking: bool
    no_breakdown: bool
    winner_rate_ok: bool

    @property
    def confirmed(self) -> bool:
        return (
            self.above_cost
            and self.not_overextended
            and self.below_recent_high
            and self.no_runup
            and self.volume_shrinking
            and self.no_breakdown
            and self.winner_rate_ok
        )


def _finite_seq(values: Sequence[float]) -> bool:
    return all(isinstance(v, int | float) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class BottomConfirmation:
    """AF-004 bottom-confirmation entry gate (implements ``EntryGate``)."""

    windows: Mapping[str, PriceWindow]
    cyq_by_code: Mapping[str, ChipCost]
    config: BottomConfirmationConfig = BottomConfirmationConfig()
    as_of_date: str | None = None
    """The date the chips were read at (``from_store``). When set, ``evaluate``
    fails closed on a decision_date mismatch so a gate built at one date can
    never confirm on a different date with stale chips (codex AF-004 P2). ``None``
    (direct construction) leaves date-consistency to the caller."""

    @classmethod
    def from_store(
        cls,
        store: SnapshotStore,
        *,
        decision_date: str,
        windows: Mapping[str, PriceWindow],
        config: BottomConfirmationConfig | None = None,
    ) -> BottomConfirmation:
        """Build the gate, reading the ``cyq_perf`` chip snapshot at the date.

        ``windows`` (PIT close/turnover per code) come from the same frame the
        screener consumes. A missing chip snapshot leaves ``cyq_by_code`` empty →
        every code fails the gate (fail-closed: no chip data, no 埋伏).
        """
        cfg = config or BottomConfirmationConfig()
        snapshot = store.latest(
            vendor=VENDOR, endpoint=ENDPOINT_CYQ, trade_date=decision_date
        )
        cyq: dict[str, ChipCost] = {}
        if snapshot is not None:
            frame = pd.read_csv(
                io.StringIO(snapshot.raw_payload.decode("utf-8")),
                dtype=str,
                keep_default_na=False,
            )
            for row in frame.itertuples(index=False):
                code = str(getattr(row, "ts_code", "")).strip()
                if not code:
                    continue
                c50 = _opt_float(getattr(row, "cost_50pct", None))
                win = _opt_float(getattr(row, "winner_rate", None))
                hh = _opt_float(getattr(row, "his_high", None))
                if c50 is None or win is None or hh is None:
                    continue
                cyq[code] = ChipCost(cost_50pct=c50, winner_rate=win, his_high=hh)
        return cls(
            windows=windows, cyq_by_code=cyq, config=cfg, as_of_date=decision_date
        )

    def evaluate(self, code: str, decision_date: str) -> BottomSignals | None:
        """Per-check bottom verdict, or ``None`` on insufficient data (fail-closed).

        ``decision_date`` is accepted for interface symmetry with the chip
        snapshot already loaded at that date; the verdict is derived purely from
        the preloaded window + chips so it replays bit-exact.
        """
        # Bound-date guard: a gate built at one date must never confirm on a
        # different date with stale chips/windows (codex AF-004 P2; PIT).
        if self.as_of_date is not None and decision_date != self.as_of_date:
            return None
        window = self.windows.get(code)
        chip = self.cyq_by_code.get(code)
        if window is None or chip is None:
            return None
        # Validate the chip facts on BOTH paths (the direct-construction path
        # bypasses from_store's CSV finiteness filter, codex AF-004 P2): a
        # non-finite / out-of-range chip fails closed rather than false-confirm.
        if (
            not math.isfinite(chip.cost_50pct)
            or chip.cost_50pct <= 0
            or not math.isfinite(chip.his_high)
            or not math.isfinite(chip.winner_rate)
            or not (0.0 <= chip.winner_rate <= 100.0)
        ):
            return None
        cfg = self.config
        closes = window.closes
        amounts = window.amounts
        n = len(closes)
        if (
            n < cfg.long_window
            or n < cfg.breakdown_window
            or len(amounts) != n
            or not _finite_seq(closes)
            or not _finite_seq(amounts)
            # A negative turnover would masquerade as 缩量 (codex AF-004 P2).
            or any(a < 0 for a in amounts)
        ):
            return None
        last = closes[-1]
        if last <= 0:
            return None

        above_cost = last >= chip.cost_50pct * (1.0 - cfg.cost_band_floor_tol)
        not_overextended = last <= chip.cost_50pct * (1.0 + cfg.cost_premium_max)
        below_recent_high = chip.his_high > 0.0 and last <= chip.his_high * (
            1.0 - cfg.high_52w_proximity
        )
        ref = closes[-cfg.long_window]
        no_runup = ref > 0 and (last / ref - 1.0) <= cfg.max_runup_pct
        recent_amt = sum(amounts[-cfg.short_window :]) / cfg.short_window
        long_amt = sum(amounts[-cfg.long_window :]) / cfg.long_window
        volume_shrinking = (
            long_amt > 0 and recent_amt <= long_amt * cfg.max_volume_shrink_ratio
        )
        window_low = min(closes[-cfg.breakdown_window :])
        no_breakdown = window_low > 0 and last >= window_low * (
            1.0 + cfg.stabilise_margin
        )
        winner_rate_ok = chip.winner_rate <= cfg.winner_rate_max

        return BottomSignals(
            above_cost=above_cost,
            not_overextended=not_overextended,
            below_recent_high=below_recent_high,
            no_runup=no_runup,
            volume_shrinking=volume_shrinking,
            no_breakdown=no_breakdown,
            winner_rate_ok=winner_rate_ok,
        )

    def confirmed(self, code: str, decision_date: str) -> bool:
        """``EntryGate`` hook: True iff ``code`` is a confirmed bottom."""
        signals = self.evaluate(code, decision_date)
        return signals is not None and signals.confirmed


def _opt_float(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


__all__ = [
    "BottomConfirmation",
    "BottomConfirmationConfig",
    "BottomSignals",
    "ChipCost",
    "PriceWindow",
]
