"""Tests for market data Pydantic models (TDD RED phase)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.models.market import (
    CapitalFlowData,
    FinancialData,
    IndexQuote,
    NewsArticle,
    SectorQuote,
    StockQuote,
    WatchlistMarketSnapshot,
)

# -- IndexQuote --


class TestIndexQuote:
    """Tests for IndexQuote frozen model."""

    def test_create_valid(self) -> None:
        quote = IndexQuote(
            code="sh000001",
            name="上证指数",
            price=3150.50,
            change_pct=0.85,
            volume=3_500_000_000.0,
            amount=450_000_000_000.0,
            timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
        )
        assert quote.code == "sh000001"
        assert quote.name == "上证指数"
        assert quote.price == 3150.50
        assert quote.change_pct == 0.85

    def test_frozen(self) -> None:
        quote = IndexQuote(
            code="sh000001",
            name="上证指数",
            price=3150.50,
            change_pct=0.85,
            volume=0.0,
            amount=0.0,
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            quote.price = 9999.0  # type: ignore[misc]

    def test_model_dump(self) -> None:
        ts = datetime(2026, 3, 22, 10, 30, tzinfo=UTC)
        quote = IndexQuote(
            code="sh000001",
            name="上证指数",
            price=3150.50,
            change_pct=0.85,
            volume=1000.0,
            amount=2000.0,
            timestamp=ts,
        )
        data = quote.model_dump()
        assert data["code"] == "sh000001"
        assert data["timestamp"] == ts


# -- StockQuote --


class TestStockQuote:
    """Tests for StockQuote frozen model."""

    def test_create_valid(self) -> None:
        quote = StockQuote(
            code="600519",
            name="贵州茅台",
            price=1800.0,
            open=1790.0,
            high=1810.0,
            low=1785.0,
            prev_close=1795.0,
            change_pct=0.28,
            volume=5_000_000.0,
            amount=9_000_000_000.0,
            turnover_rate=0.63,
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert quote.code == "600519"
        assert quote.prev_close == 1795.0
        assert quote.turnover_rate == 0.63

    def test_frozen(self) -> None:
        quote = StockQuote(
            code="600519",
            name="贵州茅台",
            price=1800.0,
            open=1790.0,
            high=1810.0,
            low=1785.0,
            prev_close=1795.0,
            change_pct=0.28,
            volume=5_000_000.0,
            amount=9_000_000_000.0,
            turnover_rate=0.63,
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            quote.code = "000001"  # type: ignore[misc]


# -- SectorQuote --


class TestSectorQuote:
    """Tests for SectorQuote frozen model."""

    def test_create_valid(self) -> None:
        quote = SectorQuote(
            name="白酒",
            change_pct=2.15,
            leader_code="600519",
            leader_name="贵州茅台",
            leader_change_pct=3.50,
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert quote.name == "白酒"
        assert quote.leader_code == "600519"


# -- CapitalFlowData --


class TestCapitalFlowData:
    """Tests for CapitalFlowData frozen model."""

    def test_create_valid(self) -> None:
        flow = CapitalFlowData(
            north_net_inflow=3_200_000_000.0,
            main_net_inflow=-1_500_000_000.0,
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert flow.north_net_inflow == 3_200_000_000.0
        assert flow.main_net_inflow == -1_500_000_000.0


# -- FinancialData --


class TestFinancialData:
    """Tests for FinancialData frozen model."""

    def test_create_with_all_fields(self) -> None:
        data = FinancialData(
            code="600519",
            name="贵州茅台",
            pe_ratio=32.5,
            pb_ratio=10.2,
            roe=30.5,
            eps=45.8,
            revenue_growth=15.3,
            report_date="2025-12-31",
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert data.pe_ratio == 32.5
        assert data.roe == 30.5

    def test_optional_fields_default_none(self) -> None:
        data = FinancialData(
            code="600519",
            name="贵州茅台",
            report_date="2025-12-31",
            timestamp=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert data.pe_ratio is None
        assert data.pb_ratio is None
        assert data.roe is None
        assert data.eps is None
        assert data.revenue_growth is None


# -- NewsArticle --


class TestNewsArticle:
    """Tests for NewsArticle frozen model."""

    def test_create_valid(self) -> None:
        article = NewsArticle(
            title="央行宣布降准50个基点",
            content="中国人民银行今日宣布...",
            source="eastmoney",
            url="https://finance.eastmoney.com/news/123",
            publish_time=datetime(2026, 3, 22, 9, 0, tzinfo=UTC),
            stock_codes=("600519", "000001"),
            importance_score=9,
        )
        assert article.title == "央行宣布降准50个基点"
        assert article.stock_codes == ("600519", "000001")
        assert article.importance_score == 9

    def test_importance_score_range_valid(self) -> None:
        for score in (0, 5, 10):
            article = NewsArticle(
                title="test",
                content="test",
                source="test",
                url="https://example.com",
                publish_time=datetime(2026, 3, 22, tzinfo=UTC),
                stock_codes=(),
                importance_score=score,
            )
            assert article.importance_score == score

    def test_importance_score_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            NewsArticle(
                title="test",
                content="test",
                source="test",
                url="https://example.com",
                publish_time=datetime(2026, 3, 22, tzinfo=UTC),
                stock_codes=(),
                importance_score=11,
            )
        with pytest.raises(ValidationError):
            NewsArticle(
                title="test",
                content="test",
                source="test",
                url="https://example.com",
                publish_time=datetime(2026, 3, 22, tzinfo=UTC),
                stock_codes=(),
                importance_score=-1,
            )

    def test_default_importance_score(self) -> None:
        article = NewsArticle(
            title="test",
            content="test",
            source="test",
            url="https://example.com",
            publish_time=datetime(2026, 3, 22, tzinfo=UTC),
        )
        assert article.importance_score == 0
        assert article.stock_codes == ()

    def test_stock_codes_is_tuple(self) -> None:
        """stock_codes should be a tuple for immutability."""
        article = NewsArticle(
            title="test",
            content="test",
            source="test",
            url="https://example.com",
            publish_time=datetime(2026, 3, 22, tzinfo=UTC),
            stock_codes=("600519",),
        )
        assert isinstance(article.stock_codes, tuple)


# -- WatchlistMarketSnapshot --


def _valid_snapshot_kwargs() -> dict[str, object]:
    return {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1800.0,
        "open": 1790.0,
        "high": 1810.0,
        "low": 1785.0,
        "prev_close": 1795.0,
        "change_pct": 0.28,
        "volume": 5_000_000.0,
        "amount": 9_000_000_000.0,
        "turnover_rate": 0.63,
        "source": "adata",
        "snapshot_at": datetime(2026, 5, 12, 6, 0, tzinfo=UTC),
    }


class TestWatchlistMarketSnapshot:
    """C-003: frozen + strict + extra='forbid' lockdown."""

    def test_create_valid(self) -> None:
        snap = WatchlistMarketSnapshot(**_valid_snapshot_kwargs())
        assert snap.code == "600519"
        assert snap.source == "adata"
        assert snap.snapshot_at == datetime(2026, 5, 12, 6, 0, tzinfo=UTC)

    def test_is_frozen(self) -> None:
        snap = WatchlistMarketSnapshot(**_valid_snapshot_kwargs())
        with pytest.raises(ValidationError):
            snap.price = 9999.0  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        kwargs = _valid_snapshot_kwargs()
        kwargs["extra_llm_field"] = "should never reach here"
        with pytest.raises(ValidationError):
            WatchlistMarketSnapshot(**kwargs)  # type: ignore[arg-type]

    def test_strict_rejects_string_for_float(self) -> None:
        """strict=True must reject string-coerced numbers from upstream."""
        kwargs = _valid_snapshot_kwargs()
        kwargs["price"] = "1800.0"
        with pytest.raises(ValidationError):
            WatchlistMarketSnapshot(**kwargs)  # type: ignore[arg-type]

    def test_code_must_be_six_digits(self) -> None:
        kwargs = _valid_snapshot_kwargs()
        kwargs["code"] = "abc"
        with pytest.raises(ValidationError):
            WatchlistMarketSnapshot(**kwargs)  # type: ignore[arg-type]
        kwargs["code"] = "60051"
        with pytest.raises(ValidationError):
            WatchlistMarketSnapshot(**kwargs)  # type: ignore[arg-type]

    def test_source_literal_set(self) -> None:
        for src in ("adata", "akshare", "unknown"):
            kwargs = _valid_snapshot_kwargs()
            kwargs["source"] = src
            snap = WatchlistMarketSnapshot(**kwargs)
            assert snap.source == src

    def test_invalid_source_rejected(self) -> None:
        kwargs = _valid_snapshot_kwargs()
        kwargs["source"] = "bloomberg"
        with pytest.raises(ValidationError):
            WatchlistMarketSnapshot(**kwargs)  # type: ignore[arg-type]
