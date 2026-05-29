"""Tests for concrete probe implementations in data_quality_probes.py (C3).

Covers:
- :class:`MarketDataQuoteProbe`: both legs, None leg raises, age computation,
  price passthrough; integration with DataQualityProvider over a fake
  MarketDataService (dual legs present → clean; one leg None → that leg
  fails; both None → quote_unavailable; divergent prices → divergence breach;
  stale timestamp → staleness/freshness breach).
- :class:`WatchlistSnapshotAgeProbe`: empty codes → 0.0; rows → max age.
- :class:`NewsAvailabilityProbe`: default (5 alive) + injected callable.
- :class:`MiroFishHealthProbe`: default (alive) + injected callable.

Mirrors the FakeQuoteProbe / FakeSnapshotProbe style in test_data_quality.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.data_quality import (
    DataQualityProvider,
    DataQualityState,
    QuoteWithAge,
)
from backend.data.data_quality_probes import (
    MarketDataQuoteProbe,
    MiroFishHealthProbe,
    NewsAvailabilityProbe,
    WatchlistSnapshotAgeProbe,
)

# Wall-clock snapshot taken once at module import to keep comparisons stable
# within a test run.  Must use a real "now" (not a fixed future constant)
# because MarketDataQuoteProbe computes age as (datetime.now(UTC) - ts).
# Using a fixed future timestamp would produce negative ages.
NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Fake MarketDataService helpers
# ---------------------------------------------------------------------------


def _make_stock_quote(price: float, *, timestamp: datetime | None = None) -> Any:
    """Return a minimal duck-typed StockQuote substitute."""
    ts = timestamp if timestamp is not None else NOW
    q = MagicMock()
    q.price = price
    q.timestamp = ts
    return q


def _make_market_data(
    primary_price: float | None,
    fallback_price: float | None,
    *,
    primary_ts: datetime | None = None,
    fallback_ts: datetime | None = None,
) -> Any:
    """Return an async-compatible fake MarketDataService.

    ``None`` for a price means that leg returns ``None`` (vendor outage).
    ``primary_ts`` / ``fallback_ts`` override the timestamp on each quote.
    """
    primary = (
        _make_stock_quote(primary_price, timestamp=primary_ts)
        if primary_price is not None
        else None
    )
    fallback = (
        _make_stock_quote(fallback_price, timestamp=fallback_ts)
        if fallback_price is not None
        else None
    )

    market_data = MagicMock()
    market_data.get_stock_realtime_dual = AsyncMock(return_value=(primary, fallback))
    return market_data


# ---------------------------------------------------------------------------
# MarketDataQuoteProbe — unit tests
# ---------------------------------------------------------------------------


class TestMarketDataQuoteProbe:
    """Unit tests for MarketDataQuoteProbe independent of DataQualityProvider."""

    @pytest.mark.asyncio
    async def test_primary_leg_returns_quote_with_age(self) -> None:
        md = _make_market_data(100.0, 101.0, primary_ts=NOW)
        probe = MarketDataQuoteProbe(md)
        # Call slightly after NOW so age > 0 is measurable (age ≈ small).
        result = await probe.get_realtime_with_age("000001", source="adata")
        assert isinstance(result, QuoteWithAge)
        assert result.source == "adata"
        assert result.price == 100.0
        assert result.snapshot_at == NOW
        # Age is computed as (datetime.now(UTC) - NOW).total_seconds().
        # In tests this is very small but non-negative.
        assert result.age_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_backup_leg_returns_quote(self) -> None:
        md = _make_market_data(100.0, 202.0, fallback_ts=NOW)
        probe = MarketDataQuoteProbe(md)
        result = await probe.get_realtime_with_age("000001", source="akshare")
        assert result.source == "akshare"
        assert result.price == 202.0

    @pytest.mark.asyncio
    async def test_none_primary_leg_raises(self) -> None:
        """None primary leg must raise so provider degrades fail-closed."""
        md = _make_market_data(None, 101.0)
        probe = MarketDataQuoteProbe(md)
        with pytest.raises(Exception):
            await probe.get_realtime_with_age("000001", source="adata")

    @pytest.mark.asyncio
    async def test_none_fallback_leg_raises(self) -> None:
        """None fallback leg must raise so provider degrades fail-closed."""
        md = _make_market_data(100.0, None)
        probe = MarketDataQuoteProbe(md)
        with pytest.raises(Exception):
            await probe.get_realtime_with_age("000001", source="akshare")

    @pytest.mark.asyncio
    async def test_age_reflects_timestamp_delta(self) -> None:
        """age_seconds should reflect (now - leg.timestamp).total_seconds()."""
        old_ts = NOW - timedelta(seconds=10)
        md = _make_market_data(100.0, 100.0, primary_ts=old_ts)
        probe = MarketDataQuoteProbe(md)
        result = await probe.get_realtime_with_age("000001", source="adata")
        # The actual age depends on wall-clock but must be >= 10s since
        # old_ts is 10s before NOW and NOW is in the past.
        assert result.age_seconds >= 10.0

    @pytest.mark.asyncio
    async def test_price_passthrough(self) -> None:
        """Price must be passed through as-is (float)."""
        md = _make_market_data(1234.56, 1235.00)
        probe = MarketDataQuoteProbe(md)
        result = await probe.get_realtime_with_age("000001", source="adata")
        assert result.price == pytest.approx(1234.56)

    @pytest.mark.asyncio
    async def test_is_suspended_defaults_false(self) -> None:
        md = _make_market_data(100.0, 100.0)
        probe = MarketDataQuoteProbe(md)
        result = await probe.get_realtime_with_age("000001", source="adata")
        assert result.is_suspended is False

    @pytest.mark.asyncio
    async def test_market_data_exception_propagates(self) -> None:
        """SDK/network errors must surface so the provider degrades."""
        md = MagicMock()
        md.get_stock_realtime_dual = AsyncMock(side_effect=ConnectionError("net err"))
        probe = MarketDataQuoteProbe(md)
        with pytest.raises(ConnectionError):
            await probe.get_realtime_with_age("000001", source="adata")


# ---------------------------------------------------------------------------
# MarketDataQuoteProbe — single-fetch per code (Fix B)
# ---------------------------------------------------------------------------


class TestMarketDataQuoteProbeSingleFetch:
    """Fix B: both legs of one evaluate() come from ONE dual fetch per code.

    Before fix B, each leg call triggered a separate get_stock_realtime_dual()
    invocation, so primary and fallback could be fetched seconds apart —
    producing spurious divergence (one leg stale, one fresh) and doubling
    latency.  After fix B, ``source="adata"`` fetches and caches; ``source=
    "akshare"`` pops the cache and returns the consistent fallback from the
    SAME snapshot.
    """

    @pytest.mark.asyncio
    async def test_adata_then_akshare_calls_dual_exactly_once(self) -> None:
        """One full evaluate round (adata + akshare) must call dual exactly once."""
        call_count = 0

        primary_q = _make_stock_quote(100.0, timestamp=NOW)
        fallback_q = _make_stock_quote(100.1, timestamp=NOW)

        async def _fake_dual(code: str):  # noqa: ANN202
            nonlocal call_count
            call_count += 1
            return primary_q, fallback_q

        md = MagicMock()
        md.get_stock_realtime_dual = _fake_dual
        probe = MarketDataQuoteProbe(md)

        await probe.get_realtime_with_age("000001", source="adata")
        await probe.get_realtime_with_age("000001", source="akshare")

        assert call_count == 1, (
            "Both legs of one evaluate() must come from a single dual fetch"
        )

    @pytest.mark.asyncio
    async def test_within_fetch_consistent_pair_does_not_trip_divergence(
        self,
    ) -> None:
        """A genuinely close within-fetch pair must NOT report divergence.

        Both legs come from the same single dual fetch so their prices are
        consistent (no across-call staleness delta).  A 0.1% difference is
        within the 0.3% threshold and must not produce quote_divergence_breach.
        """
        primary_q = _make_stock_quote(100.0, timestamp=NOW)
        fallback_q = _make_stock_quote(100.1, timestamp=NOW)  # 0.1% diff

        async def _fake_dual(code: str):  # noqa: ANN202
            return primary_q, fallback_q

        md = MagicMock()
        md.get_stock_realtime_dual = _fake_dual
        probe = MarketDataQuoteProbe(md)

        from backend.data.data_quality import DataQualityProvider
        provider = DataQualityProvider(
            quote_probe=probe,
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),
            mirofish_probe=MiroFishHealthProbe(),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.quote_divergence_breach is False

    @pytest.mark.asyncio
    async def test_within_fetch_genuinely_divergent_pair_trips_divergence(
        self,
    ) -> None:
        """A 5%-divergent within-fetch pair (same dual call) must trip divergence.

        The probe correctly reflects the vendor's own divergence, not a
        cross-call staleness artifact.
        """
        primary_q = _make_stock_quote(100.0, timestamp=NOW)
        fallback_q = _make_stock_quote(105.0, timestamp=NOW)  # 5% diff

        async def _fake_dual(code: str):  # noqa: ANN202
            return primary_q, fallback_q

        md = MagicMock()
        md.get_stock_realtime_dual = _fake_dual
        probe = MarketDataQuoteProbe(md)

        from backend.data.data_quality import DataQualityProvider
        provider = DataQualityProvider(
            quote_probe=probe,
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),
            mirofish_probe=MiroFishHealthProbe(),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.quote_divergence_breach is True
        assert state.is_acceptable_for_buy_sell is False

    @pytest.mark.asyncio
    async def test_different_codes_each_trigger_their_own_dual_fetch(self) -> None:
        """Two different codes each need their own dual fetch (no cross-code reuse)."""
        call_count = 0

        async def _fake_dual(code: str):  # noqa: ANN202
            nonlocal call_count
            call_count += 1
            q = _make_stock_quote(100.0 if code == "000001" else 200.0, timestamp=NOW)
            return q, q

        md = MagicMock()
        md.get_stock_realtime_dual = _fake_dual
        probe = MarketDataQuoteProbe(md)

        await probe.get_realtime_with_age("000001", source="adata")
        await probe.get_realtime_with_age("000001", source="akshare")
        await probe.get_realtime_with_age("000002", source="adata")
        await probe.get_realtime_with_age("000002", source="akshare")

        # Two codes → two dual fetches.
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_akshare_without_preceding_adata_fetches_defensively(self) -> None:
        """akshare leg called without preceding adata → defensive fresh fetch."""
        call_count = 0
        fallback_q = _make_stock_quote(99.0, timestamp=NOW)

        async def _fake_dual(code: str):  # noqa: ANN202
            nonlocal call_count
            call_count += 1
            return _make_stock_quote(100.0, timestamp=NOW), fallback_q

        md = MagicMock()
        md.get_stock_realtime_dual = _fake_dual
        probe = MarketDataQuoteProbe(md)

        result = await probe.get_realtime_with_age("000001", source="akshare")
        assert call_count == 1
        assert result.price == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# MarketDataQuoteProbe + DataQualityProvider integration
# ---------------------------------------------------------------------------


def _make_provider(
    primary_price: float | None,
    fallback_price: float | None,
    *,
    primary_ts: datetime | None = None,
    fallback_ts: datetime | None = None,
) -> DataQualityProvider:
    """Helper: wire a DataQualityProvider with a fake MarketDataService."""
    md = _make_market_data(
        primary_price,
        fallback_price,
        primary_ts=primary_ts,
        fallback_ts=fallback_ts,
    )
    return DataQualityProvider(
        quote_probe=MarketDataQuoteProbe(md),
        snapshot_probe=WatchlistSnapshotAgeProbe(
            market_data=md, watchlist_codes=lambda: []
        ),
        news_probe=NewsAvailabilityProbe(),
        mirofish_probe=MiroFishHealthProbe(),
    )


class TestMarketDataQuoteProbeIntegration:
    """Integration: real probe inside DataQualityProvider."""

    @pytest.mark.asyncio
    async def test_both_legs_present_no_quote_breach(self) -> None:
        """Both legs present with close prices → no quote-related breach."""
        provider = _make_provider(100.0, 100.1, primary_ts=NOW, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        assert isinstance(state, DataQualityState)
        assert state.quote_unavailable is False
        assert state.quote_divergence_breach is False
        # staleness / freshness may breach due to fetch-time timestamps
        # being wall-clock-relative; we don't assert on them here because
        # the age is real-time.

    @pytest.mark.asyncio
    async def test_primary_none_quote_not_necessarily_unavailable(self) -> None:
        """Primary None + fallback present → not both missing → no unavailable.

        With primary=None the provider sets quote_staleness_breach=True
        and minimum_freshness_breach=True (fail-closed), but
        quote_unavailable depends on both legs missing or suspension.
        """
        provider = _make_provider(None, 100.0, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        # Primary missing means the probe raised; provider _probe_quote_leg
        # returns (None, False). backup is available, so quote_unavailable
        # is False (only one leg is gone, not both). But staleness fires.
        assert state.quote_unavailable is False
        assert state.quote_staleness_breach is True  # primary missing → fail-closed

    @pytest.mark.asyncio
    async def test_both_none_quote_unavailable(self) -> None:
        """Both legs None → quote_unavailable=True → is_acceptable=False."""
        provider = _make_provider(None, None)
        state = await provider.evaluate("000001", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    @pytest.mark.asyncio
    async def test_divergent_prices_divergence_breach(self) -> None:
        """Large price divergence between legs → quote_divergence_breach."""
        # 5% divergence well above 0.3% threshold
        provider = _make_provider(100.0, 105.0, primary_ts=NOW, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        assert state.quote_divergence_breach is True
        assert state.is_acceptable_for_buy_sell is False

    @pytest.mark.asyncio
    async def test_stale_primary_staleness_breach(self) -> None:
        """Primary quote stamped 10s ago → staleness breach (threshold=5s)."""
        stale_ts = NOW - timedelta(seconds=10)
        provider = _make_provider(100.0, 100.0, primary_ts=stale_ts, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        assert state.quote_staleness_breach is True
        assert state.is_acceptable_for_buy_sell is False

    @pytest.mark.asyncio
    async def test_very_stale_primary_minimum_freshness_breach(self) -> None:
        """Primary quote 70s old → minimum_freshness_breach (threshold=60s)."""
        stale_ts = NOW - timedelta(seconds=70)
        provider = _make_provider(100.0, 100.0, primary_ts=stale_ts, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        assert state.minimum_freshness_breach is True
        assert state.is_acceptable_for_buy_sell is False

    @pytest.mark.asyncio
    async def test_close_prices_no_divergence(self) -> None:
        """Prices within 0.1% → no divergence breach."""
        provider = _make_provider(100.0, 100.1, primary_ts=NOW, fallback_ts=NOW)
        state = await provider.evaluate("000001", NOW)
        assert state.quote_divergence_breach is False

    @pytest.mark.asyncio
    async def test_quote_age_fields_populated(self) -> None:
        """Primary/backup age counters should be non-zero when legs present."""
        old_ts = NOW - timedelta(seconds=3)
        provider = _make_provider(100.0, 100.0, primary_ts=old_ts, fallback_ts=old_ts)
        state = await provider.evaluate("000001", NOW)
        assert state.primary_quote_age_seconds >= 3
        assert state.backup_quote_age_seconds >= 3


# ---------------------------------------------------------------------------
# WatchlistSnapshotAgeProbe
# ---------------------------------------------------------------------------


class TestWatchlistSnapshotAgeProbe:
    """Tests for WatchlistSnapshotAgeProbe."""

    @pytest.mark.asyncio
    async def test_empty_codes_returns_zero(self) -> None:
        """Empty watchlist → 0.0 (no staleness — no codes to be stale)."""
        md = MagicMock()
        probe = WatchlistSnapshotAgeProbe(
            market_data=md,
            watchlist_codes=lambda: [],
        )
        result = await probe.get_oldest_among_watchlist_max_age(NOW)
        assert result == 0.0
        md.get_watchlist_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_rows_return_max_age(self) -> None:
        """Max snapshot age is returned across all rows."""
        row_a = MagicMock()
        row_a.snapshot_at = NOW - timedelta(seconds=20)
        row_b = MagicMock()
        row_b.snapshot_at = NOW - timedelta(seconds=45)

        md = MagicMock()
        md.get_watchlist_snapshot = AsyncMock(return_value=[row_a, row_b])

        probe = WatchlistSnapshotAgeProbe(
            market_data=md,
            watchlist_codes=lambda: ["000001", "000002"],
        )
        result = await probe.get_oldest_among_watchlist_max_age(NOW)
        assert result == pytest.approx(45.0)

    @pytest.mark.asyncio
    async def test_empty_snapshot_rows_returns_inf(self) -> None:
        """get_watchlist_snapshot returning [] → inf (signals outage)."""
        md = MagicMock()
        md.get_watchlist_snapshot = AsyncMock(return_value=[])

        probe = WatchlistSnapshotAgeProbe(
            market_data=md,
            watchlist_codes=lambda: ["000001"],
        )
        result = await probe.get_oldest_among_watchlist_max_age(NOW)
        assert result == float("inf")

    @pytest.mark.asyncio
    async def test_snapshot_called_with_codes_and_now(self) -> None:
        """Probe passes the codes and now to get_watchlist_snapshot."""
        row = MagicMock()
        row.snapshot_at = NOW

        md = MagicMock()
        md.get_watchlist_snapshot = AsyncMock(return_value=[row])

        codes = ["510300", "510500"]
        probe = WatchlistSnapshotAgeProbe(
            market_data=md,
            watchlist_codes=lambda: codes,
        )
        await probe.get_oldest_among_watchlist_max_age(NOW)
        md.get_watchlist_snapshot.assert_called_once_with(codes, NOW)

    @pytest.mark.asyncio
    async def test_single_row_returns_its_age(self) -> None:
        row = MagicMock()
        row.snapshot_at = NOW - timedelta(seconds=30)

        md = MagicMock()
        md.get_watchlist_snapshot = AsyncMock(return_value=[row])

        probe = WatchlistSnapshotAgeProbe(
            market_data=md,
            watchlist_codes=lambda: ["000001"],
        )
        result = await probe.get_oldest_among_watchlist_max_age(NOW)
        assert result == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# NewsAvailabilityProbe
# ---------------------------------------------------------------------------


class TestNewsAvailabilityProbe:
    """Tests for NewsAvailabilityProbe."""

    @pytest.mark.asyncio
    async def test_default_returns_five(self) -> None:
        """Default (no source injected) → 5 alive — all sources healthy."""
        probe = NewsAvailabilityProbe()
        count = await probe.count_alive_sources(NOW)
        assert count == 5

    @pytest.mark.asyncio
    async def test_injected_callable_used(self) -> None:
        probe = NewsAvailabilityProbe(alive_count_source=lambda _now: 3)
        count = await probe.count_alive_sources(NOW)
        assert count == 3

    @pytest.mark.asyncio
    async def test_zero_alive_source(self) -> None:
        probe = NewsAvailabilityProbe(alive_count_source=lambda _now: 0)
        count = await probe.count_alive_sources(NOW)
        assert count == 0

    @pytest.mark.asyncio
    async def test_now_passed_to_callable(self) -> None:
        """The ``now`` argument is forwarded to the callable."""
        received: list[datetime] = []

        def capture(ts: datetime) -> int:
            received.append(ts)
            return 5

        probe = NewsAvailabilityProbe(alive_count_source=capture)
        await probe.count_alive_sources(NOW)
        assert received == [NOW]

    @pytest.mark.asyncio
    async def test_return_is_int(self) -> None:
        """Return value is always an int (coerced if callable returns float)."""
        probe = NewsAvailabilityProbe(alive_count_source=lambda _now: 5)
        result = await probe.count_alive_sources(NOW)
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_provider_uses_news_probe_correctly(self) -> None:
        """DataQualityProvider uses NewsAvailabilityProbe count for outage flag."""
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(alive_count_source=lambda _: 0),
            mirofish_probe=MiroFishHealthProbe(),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.news_outage_breach is True
        assert state.news_sources_alive_count == 0
        # news_outage is NON-blocking (P0-8 §2 redline 11)
        # Acceptance still depends on staleness/freshness (fetch-time honest),
        # but we at least confirm the news outage flag is set.
        # We can't assert is_acceptable because staleness depends on wall-clock.

    @pytest.mark.asyncio
    async def test_five_alive_no_outage(self) -> None:
        """5 alive → news_outage_breach=False."""
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),  # default = 5
            mirofish_probe=MiroFishHealthProbe(),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.news_outage_breach is False
        assert state.news_sources_alive_count == 5


# ---------------------------------------------------------------------------
# MiroFishHealthProbe
# ---------------------------------------------------------------------------


class TestMiroFishHealthProbe:
    """Tests for MiroFishHealthProbe."""

    @pytest.mark.asyncio
    async def test_default_returns_true(self) -> None:
        """Default (no source injected) → alive=True → mirofish_unavailable=False."""
        probe = MiroFishHealthProbe()
        alive = await probe.is_alive(timeout_seconds=5)
        assert alive is True

    @pytest.mark.asyncio
    async def test_injected_false_source(self) -> None:
        probe = MiroFishHealthProbe(is_alive_source=lambda: False)
        alive = await probe.is_alive(timeout_seconds=5)
        assert alive is False

    @pytest.mark.asyncio
    async def test_injected_true_source(self) -> None:
        probe = MiroFishHealthProbe(is_alive_source=lambda: True)
        alive = await probe.is_alive(timeout_seconds=5)
        assert alive is True

    @pytest.mark.asyncio
    async def test_return_is_bool(self) -> None:
        probe = MiroFishHealthProbe()
        result = await probe.is_alive(timeout_seconds=5)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_provider_mirofish_unavailable_when_dead(self) -> None:
        """DataQualityProvider sets mirofish_unavailable when probe returns False."""
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),
            mirofish_probe=MiroFishHealthProbe(is_alive_source=lambda: False),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.mirofish_unavailable is True
        # mirofish_unavailable is NON-blocking (P0-8 §2 redline 11)
        # The acceptance gate is unaffected by this flag alone.

    @pytest.mark.asyncio
    async def test_provider_mirofish_available_when_alive(self) -> None:
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),
            mirofish_probe=MiroFishHealthProbe(),  # default alive
        )
        state = await provider.evaluate("000001", NOW)
        assert state.mirofish_unavailable is False


# ---------------------------------------------------------------------------
# Non-blocking markers do not affect buy/sell gate
# ---------------------------------------------------------------------------


class TestNonBlockingMarkers:
    """P0-8 §2 redline 11 — snapshot/news/mirofish outage must NOT block."""

    @pytest.mark.asyncio
    async def test_news_outage_alone_does_not_block(self) -> None:
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(alive_count_source=lambda _: 0),
            mirofish_probe=MiroFishHealthProbe(),
        )
        state = await provider.evaluate("000001", NOW)
        # news_outage is set but the 4 blocking gates depend on quote freshness.
        # We assert that news_outage is True (wired correctly) and that
        # it shows in degradation_reason without being in is_acceptable.
        assert state.news_outage_breach is True
        # The degradation reason should mention news outage.
        assert state.degradation_reason is not None
        assert "news_outage" in (state.degradation_reason or "")
        assert state.is_acceptable_for_buy_sell is True  # P0-8 §2 redline 11

    @pytest.mark.asyncio
    async def test_mirofish_outage_alone_does_not_block(self) -> None:
        md = _make_market_data(100.0, 100.0, primary_ts=NOW, fallback_ts=NOW)
        provider = DataQualityProvider(
            quote_probe=MarketDataQuoteProbe(md),
            snapshot_probe=WatchlistSnapshotAgeProbe(
                market_data=md, watchlist_codes=lambda: []
            ),
            news_probe=NewsAvailabilityProbe(),
            mirofish_probe=MiroFishHealthProbe(is_alive_source=lambda: False),
        )
        state = await provider.evaluate("000001", NOW)
        assert state.mirofish_unavailable is True
        assert "mirofish" in (state.degradation_reason or "")
        assert state.is_acceptable_for_buy_sell is True  # P0-8 §2 redline 11
