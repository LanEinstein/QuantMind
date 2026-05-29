"""Concrete probe implementations for :class:`DataQualityProvider`.

This module wires the four :class:`typing.Protocol` probes declared in
:mod:`backend.data.data_quality` to real data sources so the provider's
``evaluate(stock_code, now)`` call produces a live :class:`DataQualityState`
instead of a clean-default no-op.

P0-8 §2 redline 8 / P1-2.B §2 redline 8: this module may NOT be in the
locked-boundary set (data_quality.py / staleness.py / divergence.py /
suspension.py), so it may import from backend.data.*.  However it must
NOT import backend.llm / backend.agents / backend.mirofish — the DQ
boundary must stay free of LLM dependency (P0-10 §2 redline 1).

Production wiring: :func:`backend.main._init_data_layer` constructs each
probe, assembles :class:`DataQualityProvider`, and attaches it to
``application.state.data_quality_provider``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from backend.data.data_quality import QuoteWithAge
from backend.data.market_data import MarketDataService


class MarketDataQuoteProbe:
    """Concrete ``PrimaryBackupQuoteProbe`` backed by :class:`MarketDataService`.

    The probe fetches the dual quote ONCE per code per evaluation round
    (``source="adata"`` first, then ``source="akshare"`` for the same code)
    by caching the ``(primary, fallback)`` tuple keyed on ``stock_code``.
    This eliminates the original two-fetches-per-code pattern that could
    produce legs captured seconds apart, causing spurious or missed divergence
    and doubling vendor round-trips.

    Cache lifecycle within one :class:`DataQualityProvider.evaluate` call:
    - ``source="adata"``: fetch dual, store ``_dual_cache[code]``, serve primary.
    - ``source="akshare"``: pop ``_dual_cache[code]``; if present serve its
      fallback (consistent within-fetch pair); else fetch fresh and serve
      fallback (defensive — backup called without preceding adata fetch).

    Concurrency note: two concurrent ``evaluate()`` calls for the SAME code
    could pop each other's cache entry; worst case is one extra dual fetch
    (both calls still get a within-fetch consistent pair from their own dual
    result).  Cross-code concurrency is fine because the cache is keyed by
    code.  This trade-off is acceptable given the ~65 evaluations/day bound
    in steady state.

    Fail-closed contract: if the requested leg is ``None`` (vendor failure),
    raise :class:`RuntimeError` so :class:`DataQualityProvider._probe_quote_leg`
    catches it and returns ``(None, False)`` — which degrades the signal to
    the appropriate breach.  The provider already wraps the call in a broad
    ``except Exception`` block (data_quality.py §399-435), so any exception
    type surfaces the same conservative outcome.

    Why fetch-time timestamps are honest:
    :func:`MarketDataService._adata_stock_row_to_quote` and
    :func:`MarketDataService._tushare_sina_row_to_quote` both stamp
    ``timestamp = datetime.now(UTC)`` at fetch time (not the vendor's
    exchange-clock timestamp).  This means ``age_seconds`` measures
    round-trip latency rather than true quote staleness, which is an
    acknowledged limitation documented in U-E2.  The divergence gate
    (|primary − backup| / primary ≤ 0.3%) remains fully effective;
    the staleness gate may be over-optimistic for in-flight quotes but is
    still the best available signal without exchange-clock mapping.
    """

    def __init__(self, market_data: MarketDataService) -> None:
        self._market_data = market_data
        # Per-code cache: populated on the adata leg, consumed on the akshare
        # leg so both legs of one evaluate() come from the same dual fetch.
        self._dual_cache: dict[str, tuple[object | None, object | None]] = {}

    async def get_realtime_with_age(
        self, stock_code: str, *, source: str
    ) -> QuoteWithAge:
        """Return a per-leg :class:`QuoteWithAge` for ``stock_code``.

        ``source="adata"`` fetches the dual snapshot, caches it for the
        imminent ``source="akshare"`` call, and returns the primary leg.
        ``source="akshare"`` pops the cached dual and returns the fallback
        leg (consistent with the primary leg because both come from the same
        single vendor round-trip).

        Args:
            stock_code: 6-digit A-share code.
            source: ``"adata"`` (primary leg) or ``"akshare"`` (backup leg).
                    The backup leg is actually sourced from Tushare/Sina as
                    of P0-8-amendment-2026-05-28; the ``"akshare"`` source
                    tag is preserved to match the Protocol's documented
                    interface (DataQualityProvider always calls with
                    ``source="akshare"`` for the backup slot).

        Raises:
            RuntimeError: when the requested leg is ``None`` so the provider
                treats it as a quote-unavailable signal for that leg.
            Exception: any SDK / network error propagates so the provider
                can degrade fail-closed.
        """
        if source == "adata":
            primary, fallback = await self._market_data.get_stock_realtime_dual(
                stock_code
            )
            # Cache the pair so the akshare call below gets the same snapshot.
            self._dual_cache[stock_code] = (primary, fallback)
            leg = primary
        else:
            # Pop the cached dual so both legs come from the same fetch;
            # if adata was never called first (unusual caller order), fetch
            # fresh defensively.
            cached = self._dual_cache.pop(stock_code, None)
            if cached is not None:
                _primary, fallback = cached
                leg = fallback
            else:
                _primary, fallback = await self._market_data.get_stock_realtime_dual(
                    stock_code
                )
                leg = fallback

        if leg is None:
            raise RuntimeError(
                f"quote leg '{source}' unavailable for {stock_code}"
            )

        now_utc = datetime.now(tz=UTC)
        age = (now_utc - leg.timestamp).total_seconds()

        return QuoteWithAge(
            source=source,
            price=float(leg.price),
            snapshot_at=leg.timestamp,
            age_seconds=age,
            is_suspended=False,  # suspension detection deferred; provider
            # folds price<=0 / NaN into quote_unavailable already via its
            # own validity guard in _probe_quote_leg (data_quality.py §458).
        )


class WatchlistSnapshotAgeProbe:
    """Concrete ``WatchlistSnapshotAgeProbe`` backed by :class:`MarketDataService`.

    Returns the largest per-code snapshot age across the active watchlist
    so a single missing code surfaces as a watchlist_snapshot_outage rather
    than averaging the problem away.

    Honesty limitation: :meth:`MarketDataService.get_watchlist_snapshot`
    stamps ``snapshot_at`` at fetch time (≈ ``now``), so this probe
    measures fetch round-trip success rather than stored-cron-staleness.
    Full stored-snapshot-age tracking (reading the most recent rows from
    MongoDB) is an explicitly deferred follow-up.  This is acceptable
    because ``watchlist_snapshot_outage`` is a **non-blocking** degraded
    marker (P0-8 §2 redline 11 / P1-2.B §2 redline 11) — it does NOT
    gate buy/sell; it only annotates the EquityPoint and ledger row.
    Gate-safe in MVP.

    Args:
        market_data: The shared :class:`MarketDataService` instance.
        watchlist_codes: Zero-argument callable returning the current list
            of active watchlist stock codes.  Injected so this probe stays
            decoupled from any Mongo / DB dependency and remains testable
            with a simple lambda.  If the callable returns an empty list,
            the probe returns ``0.0`` (no codes → no outage).
    """

    def __init__(
        self,
        market_data: MarketDataService,
        watchlist_codes: Callable[[], list[str]],
    ) -> None:
        self._market_data = market_data
        self._watchlist_codes = watchlist_codes

    async def get_oldest_among_watchlist_max_age(self, now: datetime) -> float:
        """Return the largest snapshot age in seconds across the watchlist.

        Returns ``0.0`` when the watchlist is empty — an empty watchlist
        has no stale snapshots (the scheduler has nothing to refresh).
        """
        codes = self._watchlist_codes()
        if not codes:
            return 0.0

        rows = await self._market_data.get_watchlist_snapshot(codes, now)
        if not rows:
            # All legs failed — treat as maximum possible age to signal outage.
            # DataQualityProvider._probe_snapshot_age() catches non-finite
            # values too; returning inf here triggers the outage flag cleanly.
            return float("inf")

        return max((now - r.snapshot_at).total_seconds() for r in rows)


class NewsAvailabilityProbe:
    """Concrete ``NewsAvailabilityProbe`` — cheap, no network in hot path.

    Full per-source health tracking (pinging each of the 5 news domains
    individually) is a deferred follow-up; wiring real source liveness
    requires tracking the last-seen timestamp per domain in Redis, which
    is a separate engineering task.

    This implementation accepts an injected callable so:
    (a) Tests can inject any count without real sources.
    (b) A future health-tracking module can be wired in transparently.

    Default: ``_alive_count_source`` returns 5 (all sources alive) — the
    cold-start safe value.  This makes ``news_outage_breach = False``
    until a real health tracker is wired, which is acceptable because
    news_outage is a **non-blocking** degraded marker (P0-8 §2 redline 11
    / P1-2.B §2 redline 11) and does NOT gate buy/sell.
    LLM never participates in news health judgement (P0-10 §2 redline 1).
    """

    def __init__(
        self,
        alive_count_source: Callable[[datetime], int] | None = None,
    ) -> None:
        # Default to "all 5 alive" until real health tracking is wired.
        self._alive_count_source: Callable[[datetime], int] = (
            alive_count_source if alive_count_source is not None
            else lambda _now: 5
        )

    async def count_alive_sources(self, now: datetime) -> int:
        """Return the count of alive news sources at ``now`` (0–5).

        Non-blocking: a return of 0 sets ``news_outage_breach=True`` on
        the :class:`DataQualityState` but does NOT block buy/sell routing
        (P0-8 §2 redline 11).
        """
        return int(self._alive_count_source(now))


class MiroFishHealthProbe:
    """Concrete ``MiroFishHealthProbe`` — cheap, no LLM in hot path.

    MiroFish-as-LLM lives in :mod:`backend.mirofish` and is explicitly
    forbidden from the data-quality boundary (P0-8 §2 redline 8).  This
    probe wires a pure-Python liveness signal without touching the LLM
    layer.

    Full health tracking (pinging the MiroFish simulator with an HTTP /
    side-channel health check) is a deferred follow-up.  The injected
    ``is_alive_source`` callable allows future health checks to be wired
    without modifying this class.

    Default: returns ``True`` (MiroFish alive / configured).  This makes
    ``mirofish_unavailable = False`` until a real health probe is wired,
    which is acceptable because mirofish_unavailable is a **non-blocking**
    degraded marker (P0-8 §2 redline 11) and does NOT gate buy/sell.
    LLM never participates in MiroFish health judgement (P0-10 §2 redline 1).
    """

    def __init__(
        self,
        is_alive_source: Callable[[], bool] | None = None,
    ) -> None:
        # Default to "alive" until a real health check is wired.
        self._is_alive_source: Callable[[], bool] = (
            is_alive_source if is_alive_source is not None
            else lambda: True
        )

    async def is_alive(self, *, timeout_seconds: int) -> bool:  # noqa: ARG002
        """Return ``True`` if MiroFish is considered reachable.

        ``timeout_seconds`` is accepted to satisfy the Protocol interface
        but is not used in this implementation — the injected callable
        handles any real timeout logic when a concrete health check is wired.

        Non-blocking: returning ``False`` sets ``mirofish_unavailable=True``
        on the :class:`DataQualityState` but does NOT block buy/sell routing
        (P0-8 §2 redline 11).
        """
        return bool(self._is_alive_source())


__all__ = [
    "MarketDataQuoteProbe",
    "MiroFishHealthProbe",
    "NewsAvailabilityProbe",
    "WatchlistSnapshotAgeProbe",
]
