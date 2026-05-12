"""DataQualityProvider — per-stock on-demand data quality evaluation.

This module is the **fourth buy/sell freeze source** (P0-1 + P0-5 + P0-7
circuit + here = first four; P0-9 watchlist exclusion + P1-2.A EOD
pipeline freeze round out the six). :class:`InstructionPlanBuilder`
calls :meth:`DataQualityProvider.evaluate` before reaching
:class:`backend.risk.engine.RiskEngine.validate_order`; if the resulting
:class:`DataQualityState` is not ``is_acceptable_for_buy_sell``, the
candidate is degraded to a HOLD plan with ``degradation_reason`` folded
into the rejection reason.

P1-2.B §1.5 — the binding schema — locks **7 boolean breaches + 3
counters** in :class:`DataQualityState`, with two derived properties
(``is_acceptable_for_buy_sell`` / ``degradation_reason``). Only the
first **four** breaches gate buy/sell routing; the news / MiroFish /
watchlist-snapshot signals stay informational (acceptance still
passes, but the EquityPoint and ledger row tag the run as ``degraded``).
P0-8 §2 redline 11 + P1-2.B §2 redline 11 explicitly forbid adding the
non-blocking signals to the gate — adding them is a redline violation.

P0-8 §2 redline 8 + P1-2.B §2 redline 8: this module **must not** import
``backend.llm`` / ``backend.agents``. The fail-closed evaluation is
pure-Python on signal probes; the LLM never participates in data
quality judgement (P0-10 §2 redline 1).

Fail-closed conservatism: any probe that raises is interpreted as a
breach of *its own* signal (P1-2.B §1.5.2 — "fail-closed 偏保守"). The
provider catches the exception, logs nothing (logging is the caller's
job), and synthesises a worst-case ``DataQualityState`` field for that
signal. The four blocking breaches all default to ``True`` on probe
failure so a transient infra glitch on the way to MockBroker freezes
buys/sells rather than racing through.

Dependency injection uses narrow :class:`typing.Protocol` interfaces so
this module composes with not-yet-built C-005 (multi-domain news) /
C-006 (MiroFish) / future market_data primary+backup providers without
a hard import. Production wiring lives in D-003 (InstructionPlanBuilder
five-early-return chain) and is **explicitly out of scope** for C-004.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class QuoteWithAge:
    """Probe payload returned by :class:`PrimaryBackupQuoteProbe`.

    Two providers (``adata`` primary / ``akshare`` backup) are queried
    independently so :class:`DataQualityProvider` can compute both the
    staleness gate (per-leg) and the divergence gate (cross-leg).

    Attributes:
        source: Provenance tag (``"adata"`` / ``"akshare"`` / ``"unknown"``).
        price: Last-traded price reported by the source.
        snapshot_at: Vendor timestamp on the quote (tz-aware, UTC).
        age_seconds: Pre-computed age the probe used for the
            ``staleness_threshold_seconds`` comparison. Probes always
            compute age themselves so the provider does not have to
            re-derive it from ``snapshot_at`` and the call's ``now``;
            this also keeps the gate per-leg (a stale primary does not
            mask a fresh backup). Negative ages are legal — a vendor
            clock skew that timestamps a quote a few seconds in the
            future is *not* stale (P0-8 §1.1.3 — see
            :func:`backend.data.staleness.evaluate_staleness`).
        is_suspended: Pre-computed suspension flag from
            :func:`backend.data.suspension.is_suspended` against the
            underlying watchlist snapshot row. Production probes call
            the pure helper before returning so this provider does not
            have to re-import the model. C-004 folds suspension into
            :pyattr:`DataQualityState.quote_unavailable` (P0-8 §1.6.1
            — halted stocks have no tradable quote, so quote_unavailable
            is the operationally accurate gate). Default ``False`` keeps
            the field backwards-compatible with probes that do not yet
            populate it.
    """

    source: str
    price: float
    snapshot_at: datetime
    age_seconds: float
    is_suspended: bool = False


class PrimaryBackupQuoteProbe(Protocol):
    """Per-leg quote probe (adata primary / akshare backup).

    Implementations should fail-closed-fast: any vendor / network /
    parsing error must raise — :class:`DataQualityProvider` catches the
    exception and treats it as a quote-unavailable signal for that leg.
    """

    async def get_realtime_with_age(
        self, stock_code: str, *, source: str
    ) -> QuoteWithAge:
        """Return a per-leg quote payload.

        Args:
            stock_code: 6-digit A-share code.
            source: ``"adata"`` (primary) or ``"akshare"`` (backup).

        Raises:
            Exception: Any failure surfaces; the provider degrades.
        """


class WatchlistSnapshotAgeProbe(Protocol):
    """Watchlist-wide snapshot freshness probe.

    Returns the *worst* (largest) per-code age across the active
    watchlist so a single missing code flips ``watchlist_snapshot_outage``
    rather than averaging it away.
    """

    async def get_oldest_among_watchlist_max_age(
        self, now: datetime
    ) -> float:
        """Return the largest snapshot age in seconds across the watchlist."""


class NewsAvailabilityProbe(Protocol):
    """Multi-domain news source availability probe (P0-8 §1.3).

    Returns the count of *currently alive* news sources across the five
    locked domains. Zero alive sources flips ``news_outage_breach``;
    note this does **not** block buy/sell (P0-8 §2 redline 11).
    """

    async def count_alive_sources(self, now: datetime) -> int:
        """Return the count of alive news sources at ``now`` (0-5)."""


class MiroFishHealthProbe(Protocol):
    """MiroFish service liveness probe (P0-8 §1.4).

    The provider injects this as the *health* hook, not the simulator
    invocation; MiroFish-as-LLM stays in :mod:`backend.mirofish` and
    never participates in the data-quality boundary (P0-8 §2 redline 8).
    """

    async def is_alive(self, *, timeout_seconds: int) -> bool:
        """Return True if the MiroFish service is reachable."""


@dataclass(frozen=True)
class DataQualityState:
    """Per-stock data quality result — **P1-2.B §1.5.1 locked schema**.

    Field set is frozen at 7 breach bools + 3 counters + 2 derived
    properties (12 public attributes). Adding / removing / renaming a
    field requires a ``P1-2.B-amendment-{date}-data-quality-state-fields.md``
    decision document (P1-2.B §2 redline 10).

    Attributes:
        quote_unavailable: Both primary and backup quote legs failed
            (or all snapshots indicate suspension). Blocks buy/sell.
        quote_staleness_breach: Primary quote ``age_seconds > 5``
            (P0-8 ``staleness_threshold_seconds``). Blocks buy/sell.
        quote_divergence_breach: ``|primary - backup| / primary > 0.003``
            (P0-8 ``divergence_threshold_pct``). When only one leg is
            available, conservatively True (single-source has no peer).
            Blocks buy/sell.
        minimum_freshness_breach: Primary ``age_seconds > 60`` (P0-8
            ``minimum_freshness_seconds_for_buy_sell``). Blocks buy/sell.
        news_outage_breach: All five news domains dead at ``now``.
            **Does not block** — degraded marker only.
        mirofish_unavailable: MiroFish health probe failed or timed out.
            **Does not block** — degraded marker only.
        watchlist_snapshot_outage: Watchlist 30s snapshot cron stalled
            (largest per-code age > 60s). **Does not block** — degraded.
        primary_quote_age_seconds: Surface for HOLD reason payload.
            ``0`` when the primary probe failed (sentinel "no signal").
        backup_quote_age_seconds: Same shape for the backup leg.
        news_sources_alive_count: ``0..5``; ``0`` when the probe failed.
    """

    quote_unavailable: bool
    quote_staleness_breach: bool
    quote_divergence_breach: bool
    minimum_freshness_breach: bool
    news_outage_breach: bool
    mirofish_unavailable: bool
    watchlist_snapshot_outage: bool

    primary_quote_age_seconds: int
    backup_quote_age_seconds: int
    news_sources_alive_count: int

    @property
    def is_acceptable_for_buy_sell(self) -> bool:
        """Return True when **none** of the four blocking breaches fire.

        P0-8 §2 redline 11 + P1-2.B §2 redline 11 lock the gate to
        ``quote_unavailable`` ∪ ``quote_staleness_breach`` ∪
        ``quote_divergence_breach`` ∪ ``minimum_freshness_breach``.
        Adding any of the three non-blocking breaches (news / MiroFish /
        watchlist_snapshot_outage) is a redline violation — news outage
        must not freeze core trading (the trader can still see prices
        and act on traditional quant signals).
        """
        return not (
            self.quote_unavailable
            or self.quote_staleness_breach
            or self.quote_divergence_breach
            or self.minimum_freshness_breach
        )

    @property
    def degradation_reason(self) -> str | None:
        """Return ``+``-joined breach list for HOLD reason; None when clean.

        The string is consumed by :class:`InstructionPlanBuilder` to
        compose the rejection reason on the HOLD plan and by the
        front-end "Reason" drawer (P1-5 §1.5 three-tab namespacing).
        Order is fixed (blocking first, then informational) so audit
        scripts can grep the prefix deterministically.

        Reason refinement (codex cycle 5 P3):
        * When ``quote_unavailable`` is set, it subsumes the per-leg
          staleness / divergence / freshness signals — the primary is
          either missing or suspended and explaining "age 0s > 5s"
          would contradict the actual signal.
        * When the primary leg failed (``quote_staleness_breach=True``
          with ``primary_quote_age_seconds==0``), emit
          ``primary_quote_unavailable`` instead of an
          age-vs-threshold formatted reason, since "0s > 5s" reads as
          a contradiction.
        """
        reasons: list[str] = []
        if self.quote_unavailable:
            reasons.append("quote_unavailable")
        else:
            if self.quote_staleness_breach:
                if self.primary_quote_age_seconds > 0:
                    reasons.append(
                        f"quote_staleness({self.primary_quote_age_seconds}s>5s)"
                    )
                else:
                    reasons.append("primary_quote_unavailable")
            if self.quote_divergence_breach:
                reasons.append("quote_divergence>0.3%")
            if (
                self.minimum_freshness_breach
                and self.primary_quote_age_seconds > 0
            ):
                reasons.append("minimum_freshness<60s")
        if self.news_outage_breach:
            reasons.append(
                f"news_outage({self.news_sources_alive_count}/5)"
            )
        if self.mirofish_unavailable:
            reasons.append("mirofish_unavailable")
        if self.watchlist_snapshot_outage:
            reasons.append("watchlist_snapshot_outage")
        return "+".join(reasons) if reasons else None


# P0-8 §1.1.2 + P1-2.B §1.5.2 — thresholds are config in the binding
# decisions but pinned as module constants here so they are immutable
# at runtime (P0-7 §2 redline 14 forbids hot-reload on data-quality
# thresholds). Changing any value requires an amendment + restart.
STALENESS_THRESHOLD_SECONDS: float = 5.0
DIVERGENCE_THRESHOLD_PCT: float = 0.003
MINIMUM_FRESHNESS_SECONDS_FOR_BUY_SELL: float = 60.0
WATCHLIST_SNAPSHOT_OUTAGE_SECONDS: float = 60.0
NEWS_OUTAGE_ALIVE_COUNT_THRESHOLD: int = 0  # outage when alive == 0
MIROFISH_HEALTH_TIMEOUT_SECONDS: int = 5


class DataQualityProvider:
    """Per-stock on-demand data-quality probe aggregator.

    The provider is constructed once at startup with the four signal
    probes and consulted on every InstructionPlanBuilder early-return
    chain run (P1-2.B §1.5.3). It is intentionally stateless — every
    :meth:`evaluate` call re-queries the probes. P1-2.B §2 redline 9
    locks ``evaluate`` as the **only** public method; adding
    ``evaluate_global`` / ``snapshot`` requires an amendment.

    Call volume is bounded by P0-9 (13 codes × ~5 orders / day ≈ 65
    evaluations / day in steady state), so the no-cache design pays a
    handful of additional vendor RTTs in exchange for never serving a
    stale view of the freeze gate.
    """

    def __init__(
        self,
        *,
        quote_probe: PrimaryBackupQuoteProbe,
        snapshot_probe: WatchlistSnapshotAgeProbe,
        news_probe: NewsAvailabilityProbe,
        mirofish_probe: MiroFishHealthProbe,
    ) -> None:
        self._quote_probe = quote_probe
        self._snapshot_probe = snapshot_probe
        self._news_probe = news_probe
        self._mirofish_probe = mirofish_probe

    async def evaluate(
        self, stock_code: str, now: datetime
    ) -> DataQualityState:
        """Compute a fresh :class:`DataQualityState` for ``stock_code``.

        The seven signals are probed independently. Each probe that
        raises is recorded as a worst-case signal (see module
        docstring); the provider never re-raises — failing-closed on
        an exception still produces a usable ``DataQualityState`` whose
        ``is_acceptable_for_buy_sell`` reflects the conservative gate.

        Args:
            stock_code: 6-digit A-share code being evaluated.
            now: Evaluation wall-clock. Caller is expected to pass a
                tz-aware datetime; the provider does not re-derive ages
                from ``now`` (each probe owns its own age computation,
                see :class:`QuoteWithAge`).

        Returns:
            DataQualityState: Fully-populated frozen result.
        """
        primary, primary_suspended = await self._probe_quote_leg(
            stock_code, source="adata"
        )
        backup, backup_suspended = await self._probe_quote_leg(
            stock_code, source="akshare"
        )

        # Suspension (P0-8 §1.6.1) folds into quote_unavailable so a halted
        # stock cannot be traded even when both vendor legs answer cleanly.
        # ``primary_suspended`` / ``backup_suspended`` are captured from
        # the raw payload before the price-validity guard drops the leg,
        # so a provider that reports ``is_suspended=True`` *together with*
        # a halt price sentinel (``price <= 0``) still folds into the
        # gate (codex cycle 7 P1).
        either_suspended = primary_suspended or backup_suspended

        primary_ok = primary is not None and not primary_suspended
        backup_ok = backup is not None and not backup_suspended

        quote_unavailable = (primary is None and backup is None) or either_suspended

        if primary_ok:
            quote_staleness_breach = (
                primary.age_seconds > STALENESS_THRESHOLD_SECONDS
            )
        else:
            quote_staleness_breach = True

        if primary_ok and backup_ok and primary.price > 0:
            rel = abs(primary.price - backup.price) / primary.price
            quote_divergence_breach = rel > DIVERGENCE_THRESHOLD_PCT
        else:
            # Single-source has no peer to compare against — defer to
            # the staleness / quote_unavailable gates per P1-2.B §1.5.2
            # *code* (``quote_divergence_breach = quote_unavailable``):
            # when both legs are missing the gate stays blocked via
            # quote_unavailable; when only one leg is missing the
            # divergence signal stays clean and staleness / freshness
            # handle the missing-leg case. This also aligns with
            # :func:`backend.data.divergence.evaluate_divergence`'s
            # ``fallback_price=None`` semantics.
            quote_divergence_breach = quote_unavailable

        if primary_ok:
            minimum_freshness_breach = (
                primary.age_seconds > MINIMUM_FRESHNESS_SECONDS_FOR_BUY_SELL
            )
        else:
            minimum_freshness_breach = True

        alive_count, news_outage_breach = await self._probe_news(now)
        mirofish_unavailable = await self._probe_mirofish()
        watchlist_snapshot_outage = await self._probe_snapshot_age(now)

        return DataQualityState(
            quote_unavailable=quote_unavailable,
            quote_staleness_breach=quote_staleness_breach,
            quote_divergence_breach=quote_divergence_breach,
            minimum_freshness_breach=minimum_freshness_breach,
            news_outage_breach=news_outage_breach,
            mirofish_unavailable=mirofish_unavailable,
            watchlist_snapshot_outage=watchlist_snapshot_outage,
            primary_quote_age_seconds=(
                math.ceil(max(0.0, primary.age_seconds))
                if primary is not None
                else 0
            ),
            backup_quote_age_seconds=(
                math.ceil(max(0.0, backup.age_seconds))
                if backup is not None
                else 0
            ),
            news_sources_alive_count=alive_count,
        )

    async def _probe_quote_leg(
        self, stock_code: str, *, source: str
    ) -> tuple[QuoteWithAge | None, bool]:
        """Probe one leg; return ``(normalised_payload, is_suspended)``.

        The suspension flag is captured from the *raw* probe payload
        before the validity guard runs, so a leg with both
        ``is_suspended=True`` *and* a halt price sentinel
        (``price <= 0``) still surfaces the suspension signal —
        :meth:`evaluate` folds it into ``either_suspended`` /
        ``quote_unavailable`` regardless of whether the leg's normalised
        payload is dropped (codex cycle 7 P1).

        ``payload`` is ``None`` when **any** of the following holds:

        * Probe raised.
        * ``price`` or ``age_seconds`` is not finite (NaN / inf — pandas
          propagates these from vendor brown-outs).
        * ``price`` is non-positive (``<= 0``). Zero or negative prices
          are a common halt / vendor-brownout sentinel (codex cycle 6
          P1) and an untradable quote should never satisfy the gate.

        A successful leg is returned as a *new* :class:`QuoteWithAge`
        (not the probe's object) so legacy payloads that omit the
        ``is_suspended`` field gracefully default to ``False`` instead
        of raising ``AttributeError`` in the caller (codex cycle 6 P2).

        When the probe itself raises the suspension flag defaults to
        ``False`` — we cannot trust an absent payload to be reporting
        suspension, only an explicit "leg failed".
        """
        try:
            quote = await self._quote_probe.get_realtime_with_age(
                stock_code, source=source
            )
        except Exception:
            return None, False

        # Capture is_suspended BEFORE the validity guard so a "halted +
        # bogus price" combination still surfaces the suspension signal.
        try:
            suspended = bool(getattr(quote, "is_suspended", False))
        except Exception:
            suspended = False

        try:
            price = float(quote.price)
            age = float(quote.age_seconds)
        except Exception:
            return None, suspended

        # Halt-sentinel price (non-positive or NaN) matches
        # :func:`backend.data.suspension.is_suspended` heuristics, so
        # the leg's data is itself a suspension signal regardless of
        # whether the probe set ``is_suspended=True`` explicitly. This
        # keeps the data-quality boundary fail-closed when a vendor
        # propagates the halt as a sentinel price rather than a flag
        # (codex cycle 9 P2). NaN ``age_seconds`` is *not* treated as a
        # suspension signal — only price-shaped halt patterns are.
        if not math.isfinite(price) or price <= 0:
            return None, True
        if not math.isfinite(age):
            return None, suspended

        # Final construction goes through a guarded block so a duck-typed
        # payload missing ``snapshot_at`` (or any other required field)
        # still degrades to a leg failure instead of leaking an
        # ``AttributeError`` into :meth:`evaluate` (codex cycle 8 P2).
        try:
            return (
                QuoteWithAge(
                    source=str(getattr(quote, "source", source)),
                    price=price,
                    snapshot_at=quote.snapshot_at,
                    age_seconds=age,
                    is_suspended=suspended,
                ),
                suspended,
            )
        except Exception:
            return None, suspended

    async def _probe_news(self, now: datetime) -> tuple[int, bool]:
        """Probe news availability; return ``(alive_count, outage_breach)``.

        A failed probe is treated as zero alive sources → outage_breach
        True. The alive count is surfaced verbatim in
        :pyattr:`DataQualityState.news_sources_alive_count` for the
        HOLD reason payload.
        """
        try:
            alive_count = int(await self._news_probe.count_alive_sources(now))
        except Exception:
            return 0, True
        if alive_count < 0:
            # A probe returning a negative count is a contract violation;
            # treat it as zero alive (fail-closed conservative).
            return 0, True
        return alive_count, alive_count <= NEWS_OUTAGE_ALIVE_COUNT_THRESHOLD

    async def _probe_mirofish(self) -> bool:
        """Probe MiroFish health; return ``True`` when unavailable."""
        try:
            alive = await self._mirofish_probe.is_alive(
                timeout_seconds=MIROFISH_HEALTH_TIMEOUT_SECONDS
            )
            return not alive
        except Exception:
            return True

    async def _probe_snapshot_age(self, now: datetime) -> bool:
        """Probe watchlist snapshot freshness; True ⇔ stalled cron.

        Non-finite ages (NaN from an empty aggregation, inf from a
        miscomputed delta) are treated as a probe failure so the
        watchlist-snapshot outage signal stays fail-closed.
        """
        try:
            max_age = float(
                await self._snapshot_probe.get_oldest_among_watchlist_max_age(now)
            )
        except Exception:
            return True
        if not math.isfinite(max_age):
            return True
        return max_age > WATCHLIST_SNAPSHOT_OUTAGE_SECONDS


__all__ = [
    "DIVERGENCE_THRESHOLD_PCT",
    "DataQualityProvider",
    "DataQualityState",
    "MINIMUM_FRESHNESS_SECONDS_FOR_BUY_SELL",
    "MIROFISH_HEALTH_TIMEOUT_SECONDS",
    "MiroFishHealthProbe",
    "NEWS_OUTAGE_ALIVE_COUNT_THRESHOLD",
    "NewsAvailabilityProbe",
    "PrimaryBackupQuoteProbe",
    "QuoteWithAge",
    "STALENESS_THRESHOLD_SECONDS",
    "WATCHLIST_SNAPSHOT_OUTAGE_SECONDS",
    "WatchlistSnapshotAgeProbe",
]
