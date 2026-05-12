"""Tests for :class:`backend.data.data_quality.DataQualityProvider` (C-004).

Covers the **P1-2.B §1.5 locked schema** (7 breaches + 3 counters), the
**P0-8 §2 redline 11** "only 4 blocking breaches" gate, fail-closed
behavior on every probe exception, and the no-LLM-import boundary
(P0-8 §2 redline 8).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime

import pytest

from backend.data.data_quality import (
    DIVERGENCE_THRESHOLD_PCT,
    MINIMUM_FRESHNESS_SECONDS_FOR_BUY_SELL,
    STALENESS_THRESHOLD_SECONDS,
    WATCHLIST_SNAPSHOT_OUTAGE_SECONDS,
    DataQualityProvider,
    DataQualityState,
    QuoteWithAge,
)

NOW = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Probe fakes — minimal Protocol implementations the provider can DI.
# ---------------------------------------------------------------------------


@dataclass
class FakeQuoteProbe:
    """Two-leg probe; per-source override of age/price/exception/suspension."""

    primary_age: float = 1.0
    primary_price: float = 1500.0
    primary_suspended: bool = False
    backup_age: float = 2.0
    backup_price: float = 1500.0
    backup_suspended: bool = False
    raise_for: tuple[str, ...] = ()

    async def get_realtime_with_age(
        self, stock_code: str, *, source: str
    ) -> QuoteWithAge:
        if source in self.raise_for:
            raise RuntimeError(f"vendor outage on {source}")
        if source == "adata":
            return QuoteWithAge(
                source=source,
                price=self.primary_price,
                snapshot_at=NOW,
                age_seconds=self.primary_age,
                is_suspended=self.primary_suspended,
            )
        if source == "akshare":
            return QuoteWithAge(
                source=source,
                price=self.backup_price,
                snapshot_at=NOW,
                age_seconds=self.backup_age,
                is_suspended=self.backup_suspended,
            )
        raise AssertionError(f"unknown source: {source}")


@dataclass
class FakeSnapshotProbe:
    max_age_seconds: float = 30.0
    raise_exc: bool = False

    async def get_oldest_among_watchlist_max_age(
        self, now: datetime
    ) -> float:
        if self.raise_exc:
            raise RuntimeError("mongo unreachable")
        return self.max_age_seconds


@dataclass
class FakeNewsProbe:
    alive_count: int = 5
    raise_exc: bool = False

    async def count_alive_sources(self, now: datetime) -> int:
        if self.raise_exc:
            raise RuntimeError("news repo unreachable")
        return self.alive_count


@dataclass
class FakeMiroFishProbe:
    alive: bool = True
    raise_exc: bool = False

    async def is_alive(self, *, timeout_seconds: int) -> bool:
        if self.raise_exc:
            raise TimeoutError("mirofish health timeout")
        return self.alive


def make_provider(
    *,
    quote: FakeQuoteProbe | None = None,
    snapshot: FakeSnapshotProbe | None = None,
    news: FakeNewsProbe | None = None,
    mirofish: FakeMiroFishProbe | None = None,
) -> DataQualityProvider:
    return DataQualityProvider(
        quote_probe=quote or FakeQuoteProbe(),
        snapshot_probe=snapshot or FakeSnapshotProbe(),
        news_probe=news or FakeNewsProbe(),
        mirofish_probe=mirofish or FakeMiroFishProbe(),
    )


# ---------------------------------------------------------------------------
# Locked schema invariants.
# ---------------------------------------------------------------------------


class TestDataQualityStateSchema:
    """P1-2.B §1.5.1 — 7 breach + 3 counter + 2 derived properties."""

    def test_field_set_is_locked_to_ten(self) -> None:
        """Adding/removing fields requires an amendment (P1-2.B §2 redline 10)."""
        names = set(DataQualityState.__dataclass_fields__.keys())
        assert names == {
            "quote_unavailable",
            "quote_staleness_breach",
            "quote_divergence_breach",
            "minimum_freshness_breach",
            "news_outage_breach",
            "mirofish_unavailable",
            "watchlist_snapshot_outage",
            "primary_quote_age_seconds",
            "backup_quote_age_seconds",
            "news_sources_alive_count",
        }

    def test_state_is_frozen(self) -> None:
        state = DataQualityState(
            quote_unavailable=False,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=1,
            backup_quote_age_seconds=2,
            news_sources_alive_count=5,
        )
        with pytest.raises(FrozenInstanceError):
            state.quote_unavailable = True  # type: ignore[misc]

    def test_thresholds_module_constants(self) -> None:
        """Thresholds are pinned per P0-7 §2 redline 14 (no hot-reload)."""
        assert STALENESS_THRESHOLD_SECONDS == 5.0
        assert DIVERGENCE_THRESHOLD_PCT == 0.003
        assert MINIMUM_FRESHNESS_SECONDS_FOR_BUY_SELL == 60.0
        assert WATCHLIST_SNAPSHOT_OUTAGE_SECONDS == 60.0


class TestIsAcceptableGate:
    """P0-8 §2 redline 11 + P1-2.B §2 redline 11 — only 4 breaches block."""

    def _state(self, **flags: bool) -> DataQualityState:
        base: dict[str, bool | int] = {
            "quote_unavailable": False,
            "quote_staleness_breach": False,
            "quote_divergence_breach": False,
            "minimum_freshness_breach": False,
            "news_outage_breach": False,
            "mirofish_unavailable": False,
            "watchlist_snapshot_outage": False,
            "primary_quote_age_seconds": 1,
            "backup_quote_age_seconds": 1,
            "news_sources_alive_count": 5,
        }
        base.update(flags)
        return DataQualityState(**base)  # type: ignore[arg-type]

    def test_all_clean_is_acceptable(self) -> None:
        assert self._state().is_acceptable_for_buy_sell is True
        assert self._state().degradation_reason is None

    @pytest.mark.parametrize(
        "blocking_flag",
        [
            "quote_unavailable",
            "quote_staleness_breach",
            "quote_divergence_breach",
            "minimum_freshness_breach",
        ],
    )
    def test_each_blocking_flag_breaks_acceptance(
        self, blocking_flag: str
    ) -> None:
        state = self._state(**{blocking_flag: True})
        assert state.is_acceptable_for_buy_sell is False
        assert state.degradation_reason is not None
        # The blocking flag's signature shows up in the reason string.
        assert (
            blocking_flag.replace("_breach", "").split("_", 1)[0]
            in state.degradation_reason
        )

    @pytest.mark.parametrize(
        "non_blocking_flag",
        [
            "news_outage_breach",
            "mirofish_unavailable",
            "watchlist_snapshot_outage",
        ],
    )
    def test_non_blocking_flag_does_not_break_acceptance(
        self, non_blocking_flag: str
    ) -> None:
        """P0-8 §2 redline 11 — news/mirofish/snapshot outage degrade only."""
        state = self._state(**{non_blocking_flag: True})
        assert state.is_acceptable_for_buy_sell is True
        assert state.degradation_reason is not None  # still surfaces

    def test_combined_blocking_breaches_compose_reason(self) -> None:
        state = self._state(
            quote_staleness_breach=True,
            minimum_freshness_breach=True,
            primary_quote_age_seconds=120,
        )
        reason = state.degradation_reason or ""
        assert "quote_staleness(120s>5s)" in reason
        assert "minimum_freshness<60s" in reason
        assert "+" in reason

    def test_news_count_is_surfaced_in_reason(self) -> None:
        state = self._state(
            news_outage_breach=True, news_sources_alive_count=0
        )
        assert "news_outage(0/5)" in (state.degradation_reason or "")


# ---------------------------------------------------------------------------
# DataQualityProvider behavior.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEvaluateHappyPath:
    """Both legs healthy → acceptable, no breach signals."""

    async def test_fresh_aligned_legs_pass(self) -> None:
        provider = make_provider()
        state = await provider.evaluate("600519", NOW)
        assert state.is_acceptable_for_buy_sell is True
        assert state.quote_unavailable is False
        assert state.quote_staleness_breach is False
        assert state.quote_divergence_breach is False
        assert state.minimum_freshness_breach is False
        assert state.primary_quote_age_seconds == 1
        assert state.backup_quote_age_seconds == 2
        assert state.news_sources_alive_count == 5
        assert state.degradation_reason is None

    async def test_returns_data_quality_state(self) -> None:
        provider = make_provider()
        state = await provider.evaluate("600519", NOW)
        assert isinstance(state, DataQualityState)


@pytest.mark.asyncio
class TestStalenessBreach:
    async def test_primary_age_six_seconds_breaches(self) -> None:
        provider = make_provider(quote=FakeQuoteProbe(primary_age=6.0))
        state = await provider.evaluate("600519", NOW)
        assert state.quote_staleness_breach is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_primary_age_five_seconds_is_boundary_not_breach(self) -> None:
        provider = make_provider(quote=FakeQuoteProbe(primary_age=5.0))
        state = await provider.evaluate("600519", NOW)
        assert state.quote_staleness_breach is False

    async def test_minimum_freshness_breach_at_61_seconds(self) -> None:
        provider = make_provider(quote=FakeQuoteProbe(primary_age=61.0))
        state = await provider.evaluate("600519", NOW)
        assert state.minimum_freshness_breach is True
        assert state.quote_staleness_breach is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestDivergenceBreach:
    async def test_aligned_legs_no_divergence(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=1500.0, backup_price=1500.0)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_divergence_breach is False

    async def test_above_threshold_diverges(self) -> None:
        # 0.4% diff > 0.3%
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=1500.0, backup_price=1506.0)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_divergence_breach is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestPrimaryLegFailure:
    """Primary down + backup alive → staleness + freshness block; divergence
    stays clean (single-source has no peer to disagree with, P1-2.B §1.5.2
    code path ``quote_divergence_breach = quote_unavailable``)."""

    async def test_primary_exception_blocks_via_staleness_and_freshness(
        self,
    ) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(raise_for=("adata",))
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is False  # backup is alive
        assert state.quote_staleness_breach is True
        # Single-source view (primary gone): divergence defers to
        # quote_unavailable per spec.
        assert state.quote_divergence_breach is False
        assert state.minimum_freshness_breach is True
        assert state.primary_quote_age_seconds == 0
        assert state.backup_quote_age_seconds == 2
        assert state.is_acceptable_for_buy_sell is False
        # Reason names the missing primary explicitly rather than
        # rendering "0s>5s" (codex cycle 5 P3).
        reason = state.degradation_reason or ""
        assert "primary_quote_unavailable" in reason
        assert "minimum_freshness" not in reason  # sentinel age suppresses it


@pytest.mark.asyncio
class TestBackupLegFailure:
    async def test_backup_exception_keeps_trade_acceptable(self) -> None:
        """Primary fresh + backup missing → no gate fires. Trade allowed.

        Matches the divergence helper's ``fallback_price=None`` semantics:
        a missing peer is not the same as a divergent peer.
        """
        provider = make_provider(
            quote=FakeQuoteProbe(raise_for=("akshare",))
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is False
        assert state.quote_staleness_breach is False
        assert state.quote_divergence_breach is False
        assert state.minimum_freshness_breach is False
        assert state.is_acceptable_for_buy_sell is True
        assert state.backup_quote_age_seconds == 0


@pytest.mark.asyncio
class TestBothLegsDown:
    async def test_both_exceptions_yield_quote_unavailable(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(raise_for=("adata", "akshare"))
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.quote_staleness_breach is True
        # Both legs missing → divergence inherits quote_unavailable (True).
        assert state.quote_divergence_breach is True
        assert state.minimum_freshness_breach is True
        assert state.is_acceptable_for_buy_sell is False
        # quote_unavailable subsumes per-leg breach reasons (codex
        # cycle 5 P3): the audit string should not contain
        # contradictory "0s>5s" suffixes.
        reason = state.degradation_reason
        assert reason == "quote_unavailable"


@pytest.mark.asyncio
class TestNewsOutageNonBlocking:
    async def test_zero_alive_sources_marks_outage_but_passes_gate(self) -> None:
        provider = make_provider(news=FakeNewsProbe(alive_count=0))
        state = await provider.evaluate("600519", NOW)
        assert state.news_outage_breach is True
        assert state.news_sources_alive_count == 0
        # **Critical**: news outage MUST NOT block buy/sell.
        assert state.is_acceptable_for_buy_sell is True

    async def test_news_probe_exception_marks_outage(self) -> None:
        provider = make_provider(news=FakeNewsProbe(raise_exc=True))
        state = await provider.evaluate("600519", NOW)
        assert state.news_outage_breach is True
        assert state.news_sources_alive_count == 0
        assert state.is_acceptable_for_buy_sell is True

    async def test_negative_count_treated_as_zero(self) -> None:
        """Probe contract violation → fail-closed conservative."""
        provider = make_provider(news=FakeNewsProbe(alive_count=-3))
        state = await provider.evaluate("600519", NOW)
        assert state.news_sources_alive_count == 0
        assert state.news_outage_breach is True


@pytest.mark.asyncio
class TestMiroFishNonBlocking:
    async def test_mirofish_dead_marks_unavailable_but_passes_gate(self) -> None:
        provider = make_provider(mirofish=FakeMiroFishProbe(alive=False))
        state = await provider.evaluate("600519", NOW)
        assert state.mirofish_unavailable is True
        assert state.is_acceptable_for_buy_sell is True

    async def test_mirofish_timeout_is_unavailable(self) -> None:
        provider = make_provider(mirofish=FakeMiroFishProbe(raise_exc=True))
        state = await provider.evaluate("600519", NOW)
        assert state.mirofish_unavailable is True
        assert state.is_acceptable_for_buy_sell is True


@pytest.mark.asyncio
class TestWatchlistSnapshotOutageNonBlocking:
    async def test_max_age_61_seconds_breaches_but_passes_gate(self) -> None:
        provider = make_provider(snapshot=FakeSnapshotProbe(max_age_seconds=61.0))
        state = await provider.evaluate("600519", NOW)
        assert state.watchlist_snapshot_outage is True
        assert state.is_acceptable_for_buy_sell is True

    async def test_max_age_60_seconds_is_boundary_not_breach(self) -> None:
        provider = make_provider(snapshot=FakeSnapshotProbe(max_age_seconds=60.0))
        state = await provider.evaluate("600519", NOW)
        assert state.watchlist_snapshot_outage is False

    async def test_snapshot_probe_exception_marks_outage(self) -> None:
        provider = make_provider(snapshot=FakeSnapshotProbe(raise_exc=True))
        state = await provider.evaluate("600519", NOW)
        assert state.watchlist_snapshot_outage is True
        assert state.is_acceptable_for_buy_sell is True


@pytest.mark.asyncio
class TestSuspensionFlow:
    """P0-8 §1.6.1 — halted stock must freeze buy/sell via quote_unavailable."""

    async def test_primary_suspended_flips_quote_unavailable(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_suspended=True)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False
        assert "quote_unavailable" in (state.degradation_reason or "")

    async def test_backup_suspended_flips_quote_unavailable(self) -> None:
        """Even if primary is alive, suspension flag on backup freezes trade."""
        provider = make_provider(
            quote=FakeQuoteProbe(backup_suspended=True)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_clean_quote_passes_when_no_suspension(self) -> None:
        """Sanity: explicit is_suspended=False on both legs is acceptable."""
        provider = make_provider(
            quote=FakeQuoteProbe(
                primary_suspended=False, backup_suspended=False
            )
        )
        state = await provider.evaluate("600519", NOW)
        assert state.is_acceptable_for_buy_sell is True


@pytest.mark.asyncio
class TestNonFiniteQuoteLeg:
    """Codex cycle 2 [P1]: NaN/inf prices must fail the leg, not slip through.

    Without finite-ness guards, a fresh primary plus a ``NaN`` backup
    leg produced ``rel = NaN`` and ``NaN > 0.003 == False`` — the gate
    silently passed even though the backup was useless.
    """

    async def test_nan_backup_price_is_treated_as_suspension(self) -> None:
        """Codex cycle 9 [P2]: backup NaN price matches halt sentinel;
        fold into quote_unavailable via either_suspended."""
        import math as _m
        provider = make_provider(
            quote=FakeQuoteProbe(backup_price=_m.nan)
        )
        state = await provider.evaluate("600519", NOW)
        # NaN backup price → suspended inferred → quote_unavailable
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False
        assert state.backup_quote_age_seconds == 0

    async def test_inf_primary_age_falls_back_to_quote_unavailable(self) -> None:
        import math as _m
        provider = make_provider(
            quote=FakeQuoteProbe(
                primary_age=_m.inf, raise_for=("akshare",)
            )
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_nan_primary_price_drops_primary_leg(self) -> None:
        import math as _m
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=_m.nan)
        )
        state = await provider.evaluate("600519", NOW)
        # primary leg discarded → staleness + minimum_freshness breach
        assert state.quote_staleness_breach is True
        assert state.minimum_freshness_breach is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestFutureDatedQuotes:
    """Clock-skewed future-dated quotes (negative age) must not freeze trade.

    Regression for codex review cycle 1 [P2] — negative ``age_seconds`` is
    a legitimate fresh-quote signal per :mod:`backend.data.staleness`, so
    the provider must use an explicit "leg missing" sentinel (``None``)
    rather than overloading negative ages.
    """

    async def test_negative_age_is_treated_as_fresh(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_age=-3.0, backup_age=-2.0)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is False
        assert state.quote_staleness_breach is False
        assert state.quote_divergence_breach is False  # both legs present
        assert state.minimum_freshness_breach is False
        assert state.is_acceptable_for_buy_sell is True
        # Age fields surface 0 (floor) — the negative wall-clock skew
        # never leaks into the HOLD reason payload.
        assert state.primary_quote_age_seconds == 0
        assert state.backup_quote_age_seconds == 0


@pytest.mark.asyncio
class TestHaltSentinelInferredSuspension:
    """Codex cycle 9 [P2]: halt-sentinel price patterns infer suspension.

    ``backend.data.suspension.is_suspended`` treats ``price <= 0`` /
    ``price = NaN`` as suspension signals on a snapshot row. The
    DataQualityProvider mirrors that semantic when a quote leg returns
    the same patterns *without* the explicit ``is_suspended`` flag —
    the data itself is the suspension signal.
    """

    async def test_backup_zero_price_alone_freezes_trade(self) -> None:
        """Primary fresh + backup zero-priced (no suspended flag)."""
        provider = make_provider(
            quote=FakeQuoteProbe(backup_price=0.0, backup_suspended=False)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_primary_zero_price_alone_freezes_trade(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=0.0, primary_suspended=False)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_nan_age_alone_does_not_infer_suspension(self) -> None:
        """NaN age is timing weirdness, not a halt signal — only the
        price-shaped patterns infer suspension."""
        import math as _m
        provider = make_provider(
            quote=FakeQuoteProbe(primary_age=_m.nan)
        )
        state = await provider.evaluate("600519", NOW)
        # Primary dropped (NaN age) but suspended=False (default).
        # Backup fresh → quote_unavailable=False (no suspension inferred).
        assert state.quote_unavailable is False
        # Trade still blocked by staleness/freshness on missing primary.
        assert state.quote_staleness_breach is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestSuspendedAndInvalidPriceCombo:
    """Codex cycle 7 [P1]: suspension flag must propagate even when the
    same leg also has an invalid (halt-sentinel) price."""

    async def test_backup_suspended_with_zero_price_still_freezes(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(
                backup_price=0.0, backup_suspended=True
            )
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_primary_suspended_with_nan_price_still_freezes(self) -> None:
        import math as _m
        provider = make_provider(
            quote=FakeQuoteProbe(
                primary_price=_m.nan, primary_suspended=True
            )
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestNonPositivePrice:
    """Codex cycle 6 [P1]: ``price <= 0`` is a halt sentinel; reject the leg.

    Without this guard, a probe returning ``price=0.0`` (no
    ``is_suspended`` flag set) with a fresh backup leg would let
    ``quote_unavailable`` / staleness / divergence / freshness all
    stay clean, approving BUY/SELL on an invalid quote.
    """

    async def test_zero_primary_price_drops_primary_leg(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=0.0)
        )
        state = await provider.evaluate("600519", NOW)
        # Codex cycle 9 P2: halt-sentinel price infers suspension →
        # quote_unavailable=True (regardless of explicit is_suspended).
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_negative_primary_price_drops_primary_leg(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=-1.0)
        )
        state = await provider.evaluate("600519", NOW)
        # Negative price = halt sentinel → suspension inferred.
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_zero_price_both_legs_yields_quote_unavailable(self) -> None:
        provider = make_provider(
            quote=FakeQuoteProbe(primary_price=0.0, backup_price=0.0)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True


@pytest.mark.asyncio
class TestLegacyQuotePayload:
    """Codex cycle 6 [P2]: payloads missing ``is_suspended`` must not crash.

    Probes implemented before the suspension field landed (or older
    cache rounds) can return a duck-typed object without
    ``is_suspended``. The provider must coerce the missing attribute to
    ``False`` rather than letting ``AttributeError`` escape.
    """

    async def test_payload_without_is_suspended_does_not_raise(self) -> None:
        from datetime import UTC, datetime

        class LegacyQuote:
            """Pre-C-004 probe payload: no is_suspended attribute."""
            source = "adata"
            price = 1500.0
            snapshot_at = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
            age_seconds = 1.0

        class LegacyProbe:
            async def get_realtime_with_age(
                self, stock_code: str, *, source: str
            ) -> QuoteWithAge:
                return LegacyQuote()  # type: ignore[return-value]

        provider = DataQualityProvider(
            quote_probe=LegacyProbe(),
            snapshot_probe=FakeSnapshotProbe(),
            news_probe=FakeNewsProbe(),
            mirofish_probe=FakeMiroFishProbe(),
        )
        state = await provider.evaluate("600519", NOW)
        # Legacy payload is treated as fresh + not-suspended (default).
        assert state.is_acceptable_for_buy_sell is True


@pytest.mark.asyncio
class TestSingleSourceDivergenceSpec:
    """Codex cycle 5 [P2]: single-source must not freeze trade.

    P1-2.B §1.5.2 *code* says
    ``quote_divergence_breach = quote_unavailable`` when a single leg
    is missing. The mismatched inline comment ("conservative breach")
    is not the binding semantic — the code is.
    """

    async def test_primary_only_does_not_freeze_divergence(self) -> None:
        """Primary fresh + backup missing: no peer to disagree with,
        the gate must defer to staleness/availability signals."""
        provider = make_provider(
            quote=FakeQuoteProbe(raise_for=("akshare",))
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_divergence_breach is False
        assert state.is_acceptable_for_buy_sell is True

    async def test_backup_only_does_not_freeze_divergence(self) -> None:
        """Primary missing + backup fresh: gate is blocked via
        staleness/freshness on the primary leg, *not* divergence."""
        provider = make_provider(
            quote=FakeQuoteProbe(raise_for=("adata",))
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_divergence_breach is False
        # Trade still blocked by staleness/freshness on primary.
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestMalformedQuotePayload:
    """Codex cycle 4 [P2]: a malformed probe payload must fail closed.

    Probes are external code (vendor cache rounds, fake doubles, future
    C-005 / C-006 implementations) and may return shapes that violate
    the :class:`QuoteWithAge` contract — ``price=None``, a dict-like
    object without ``age_seconds``, etc. The provider must not raise
    out of :meth:`evaluate`; it must synthesise a fail-closed
    :class:`DataQualityState` instead.
    """

    async def test_none_price_payload_does_not_raise(self) -> None:
        """A payload with ``price=None`` (e.g. round-tripped cache miss)
        must be swallowed as a leg failure, not crash with TypeError."""

        class BrokenQuote:
            async def get_realtime_with_age(
                self, stock_code: str, *, source: str
            ) -> QuoteWithAge:
                # Force a payload whose .price isn't a real number.
                # Pydantic-free probe stubs can produce these; the
                # provider must defend against them.
                obj = QuoteWithAge(
                    source=source, price=1.0, snapshot_at=NOW,
                    age_seconds=1.0,
                )
                object.__setattr__(obj, "price", None)  # type: ignore[arg-type]
                return obj

        provider = DataQualityProvider(
            quote_probe=BrokenQuote(),
            snapshot_probe=FakeSnapshotProbe(),
            news_probe=FakeNewsProbe(),
            mirofish_probe=FakeMiroFishProbe(),
        )
        state = await provider.evaluate("600519", NOW)
        # Both legs return malformed payloads → quote_unavailable.
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_missing_age_attribute_does_not_raise(self) -> None:
        """A probe that returns an object without ``age_seconds``
        attribute must also fail closed."""

        class HeadlessPayload:
            """No age_seconds / no price — completely off-contract."""
            source = "adata"

        class BrokenQuote:
            async def get_realtime_with_age(
                self, stock_code: str, *, source: str
            ) -> QuoteWithAge:
                return HeadlessPayload()  # type: ignore[return-value]

        provider = DataQualityProvider(
            quote_probe=BrokenQuote(),
            snapshot_probe=FakeSnapshotProbe(),
            news_probe=FakeNewsProbe(),
            mirofish_probe=FakeMiroFishProbe(),
        )
        state = await provider.evaluate("600519", NOW)
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False

    async def test_missing_snapshot_at_does_not_raise(self) -> None:
        """Codex cycle 8 [P2]: payload missing ``snapshot_at`` field.

        A duck-typed payload that has price+age but no snapshot_at used
        to raise AttributeError on the QuoteWithAge construction line.
        Now the leg must drop fail-closed.
        """

        class NoSnapshotPayload:
            source = "adata"
            price = 1500.0
            age_seconds = 1.0
            # snapshot_at intentionally absent

        class BrokenQuote:
            async def get_realtime_with_age(
                self, stock_code: str, *, source: str
            ) -> QuoteWithAge:
                return NoSnapshotPayload()  # type: ignore[return-value]

        provider = DataQualityProvider(
            quote_probe=BrokenQuote(),
            snapshot_probe=FakeSnapshotProbe(),
            news_probe=FakeNewsProbe(),
            mirofish_probe=FakeMiroFishProbe(),
        )
        # Must not raise.
        state = await provider.evaluate("600519", NOW)
        # Both legs drop → quote_unavailable.
        assert state.quote_unavailable is True
        assert state.is_acceptable_for_buy_sell is False


@pytest.mark.asyncio
class TestSnapshotProbeNonFinite:
    """Codex cycle 3 [P3]: NaN/inf max-age must fail closed."""

    async def test_nan_max_age_marks_outage(self) -> None:
        import math as _m
        provider = make_provider(
            snapshot=FakeSnapshotProbe(max_age_seconds=_m.nan)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.watchlist_snapshot_outage is True

    async def test_inf_max_age_marks_outage(self) -> None:
        import math as _m
        provider = make_provider(
            snapshot=FakeSnapshotProbe(max_age_seconds=_m.inf)
        )
        state = await provider.evaluate("600519", NOW)
        assert state.watchlist_snapshot_outage is True


@pytest.mark.asyncio
class TestFractionalAgeAudit:
    """Codex cycle 3 [P3]: fractional ages must surface ceiling, not floor.

    With ``int()`` truncation, a 5.9s age would surface as ``5`` and the
    reason string would read ``quote_staleness(5s>5s)`` — visually
    contradicting the gate decision.
    """

    async def test_fractional_breach_surfaces_ceiling(self) -> None:
        provider = make_provider(quote=FakeQuoteProbe(primary_age=5.9))
        state = await provider.evaluate("600519", NOW)
        assert state.quote_staleness_breach is True
        # Surfaced int must be ≥ the actual age so the reason string
        # doesn't visually equal the threshold.
        assert state.primary_quote_age_seconds == 6
        assert "quote_staleness(6s>5s)" in (state.degradation_reason or "")

    async def test_exact_integer_age_not_inflated(self) -> None:
        """5.0s is exactly the threshold — not stale; counter still 5."""
        provider = make_provider(quote=FakeQuoteProbe(primary_age=5.0))
        state = await provider.evaluate("600519", NOW)
        assert state.quote_staleness_breach is False
        assert state.primary_quote_age_seconds == 5


@pytest.mark.asyncio
class TestPublicSurface:
    """P1-2.B §2 redline 9 — only one public method: evaluate."""

    async def test_evaluate_is_the_only_public_async_method(self) -> None:
        provider = make_provider()
        public = [
            name for name in dir(provider)
            if not name.startswith("_") and callable(getattr(provider, name))
        ]
        assert public == ["evaluate"], (
            f"DataQualityProvider must expose only `evaluate` (P1-2.B §2 "
            f"redline 9); found extra: {set(public) - {'evaluate'}}"
        )


class TestNoLlmImport:
    """P0-8 §2 redline 8 — data_quality.py must not import backend.llm/agents."""

    def test_data_quality_module_does_not_import_llm_or_agents(self) -> None:
        from pathlib import Path

        src_files = [
            Path("backend/data/data_quality.py"),
            Path("backend/data/staleness.py"),
            Path("backend/data/divergence.py"),
            Path("backend/data/suspension.py"),
        ]
        for src in src_files:
            text = src.read_text()
            assert "import backend.llm" not in text, f"{src} imports backend.llm"
            assert "from backend.llm" not in text, f"{src} imports backend.llm"
            assert "import backend.agents" not in text, (
                f"{src} imports backend.agents"
            )
            assert "from backend.agents" not in text, (
                f"{src} imports backend.agents"
            )
            assert "import backend.risk" not in text, (
                f"{src} imports backend.risk"
            )
            assert "from backend.risk" not in text, (
                f"{src} imports backend.risk"
            )
