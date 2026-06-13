"""O-001 deterministic info digest tests.

Locks:
* Zero LLM — module imports no backend.{llm,agents,agents_team} (AST).
* Deterministic — same inputs → bit-exact same digest document.
* Evidence-only — persistence uses the locked NEWS- / MARKET- prefixes,
  validated through the single P0-8 regex; the writer is idempotent per
  trade_date (duplicate id → False, never a second row).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from backend.mirofish.digest_evidence import (
    InfoDigestEvidenceError,
    InfoDigestEvidenceWriter,
    build_market_digest_evidence,
    build_news_digest_evidence,
    make_market_digest_evidence_id,
    make_news_digest_evidence_id,
)
from backend.mirofish.info_digest import (
    DailyStockRow,
    IndexBar,
    build_info_digest,
    compute_index_trend,
    compute_sector_heat,
    compute_sentiment_indicators,
    render_digest_text,
    render_market_sections,
    render_news_section,
    summarize_news,
)
from backend.models.evidence import parse_evidence_prefix, validate_evidence_id
from backend.models.market import NewsArticle

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRADE_DATE = "2026-06-12"


def _article(
    title: str,
    *,
    domain: str = "financial",
    source: str = "eastmoney",
    importance: int = 5,
    url: str = "",
) -> NewsArticle:
    return NewsArticle(
        title=title,
        content=f"{title} 正文",
        source=source,
        url=url or f"https://example.com/{abs(hash(title))}",
        publish_time=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
        importance_score=importance,
        domain=domain,  # type: ignore[arg-type]
    )


def _rows() -> tuple[DailyStockRow, ...]:
    # Two sectors with 3 constituents each + one unmapped code + one
    # single-stock sector (skipped: below min constituents).
    return (
        DailyStockRow(code="600001.SH", pct_chg=2.0, amount=1000.0),
        DailyStockRow(code="600002.SH", pct_chg=1.0, amount=1000.0),
        DailyStockRow(code="600003.SH", pct_chg=-0.5, amount=2000.0),
        DailyStockRow(code="000001.SZ", pct_chg=-1.0, amount=500.0),
        DailyStockRow(code="000002.SZ", pct_chg=-2.0, amount=500.0),
        DailyStockRow(code="000003.SZ", pct_chg=-3.0, amount=500.0),
        DailyStockRow(code="688001.SH", pct_chg=5.0, amount=300.0),
        DailyStockRow(code="999999.SH", pct_chg=9.0, amount=100.0),
    )


INDUSTRY = {
    "600001.SH": "半导体",
    "600002.SH": "半导体",
    "600003.SH": "半导体",
    "000001.SZ": "银行",
    "000002.SZ": "银行",
    "000003.SZ": "银行",
    "688001.SH": "光伏",  # only 1 constituent → sector skipped
    # 999999.SH intentionally unmapped
}


# ---------------------------------------------------------------------------
# Index trend
# ---------------------------------------------------------------------------


class TestIndexTrend:
    def test_full_history_returns(self) -> None:
        closes = tuple(float(100 + i) for i in range(30))  # 100..129
        bar = IndexBar(code="000300.SH", name="沪深300", closes=closes)
        trend = compute_index_trend(bar)
        assert trend.last_close == 129.0
        assert trend.ret_1d == pytest.approx((129 / 128 - 1) * 100)
        assert trend.ret_5d == pytest.approx((129 / 124 - 1) * 100)
        assert trend.ret_20d == pytest.approx((129 / 109 - 1) * 100)
        # last close above the 20-day mean (rising series)
        assert trend.above_ma20 is True
        assert trend.vol_20d is not None and trend.vol_20d > 0

    def test_insufficient_history_degrades_to_none(self) -> None:
        bar = IndexBar(code="000001.SH", name="上证", closes=(100.0, 101.0))
        trend = compute_index_trend(bar)
        assert trend.ret_1d == pytest.approx(1.0)
        assert trend.ret_5d is None
        assert trend.ret_20d is None
        assert trend.above_ma20 is None
        assert trend.vol_20d is None

    def test_empty_closes(self) -> None:
        bar = IndexBar(code="000001.SH", name="上证", closes=())
        trend = compute_index_trend(bar)
        assert trend.last_close is None
        assert trend.ret_1d is None

    def test_non_positive_close_fails_closed(self) -> None:
        closes = (100.0, 0.0, 102.0)
        trend = compute_index_trend(
            IndexBar(code="X", name="x", closes=closes)
        )
        # 1d return uses closes[-2]=0 → undefined → None, never inf
        assert trend.ret_1d is None


# ---------------------------------------------------------------------------
# Sector heat
# ---------------------------------------------------------------------------


class TestSectorHeat:
    def test_grouping_and_ranking(self) -> None:
        heat = compute_sector_heat(_rows(), INDUSTRY)
        names = [h.sector for h in heat]
        # 光伏 (1 constituent) and the unmapped code are excluded.
        assert names == ["半导体", "银行"]
        semi = heat[0]
        assert semi.stock_count == 3
        assert semi.advancers == 2
        assert semi.decliners == 1
        assert semi.mean_pct_chg == pytest.approx((2.0 + 1.0 - 0.5) / 3)
        # amount share over grouped sectors only (4000 + 1500)
        assert semi.amount_share == pytest.approx(4000 / 5500)
        assert heat[0].heat_score >= heat[1].heat_score

    def test_deterministic(self) -> None:
        a = compute_sector_heat(_rows(), INDUSTRY)
        b = compute_sector_heat(tuple(reversed(_rows())), dict(INDUSTRY))
        assert a == b

    def test_empty_inputs(self) -> None:
        assert compute_sector_heat((), {}) == ()


# ---------------------------------------------------------------------------
# Sentiment proxies
# ---------------------------------------------------------------------------


class TestSentiment:
    def test_breadth_and_limits(self) -> None:
        rows = (
            DailyStockRow(code="600001.SH", pct_chg=10.0, amount=1.0),
            DailyStockRow(code="300001.SZ", pct_chg=10.0, amount=1.0),
            DailyStockRow(code="300002.SZ", pct_chg=19.9, amount=1.0),
            DailyStockRow(code="000001.SZ", pct_chg=-9.9, amount=1.0),
            DailyStockRow(code="000002.SZ", pct_chg=0.0, amount=1.0),
        )
        s = compute_sentiment_indicators(rows, ())
        assert (s.advancers, s.decliners, s.flat) == (3, 1, 1)
        # 600001 at +10 hits the 9.8 main-board proxy threshold;
        # 300001 at +10 does NOT hit the 19.8 ChiNext threshold;
        # 300002 at +19.9 does. 000001 at -9.9 is a limit-down proxy.
        assert s.limit_up_count == 2
        assert s.limit_down_count == 1

    def test_cross_domain_echo_and_importance(self) -> None:
        news = (
            _article("芯片出口管制 升级", domain="financial", importance=8),
            _article("芯片出口管制升级", domain="global", importance=6),
            _article("无关新闻", domain="financial", importance=2),
        )
        s = compute_sentiment_indicators((), news)
        assert s.cross_domain_echo_count == 1
        assert s.high_importance_news_count == 1


# ---------------------------------------------------------------------------
# News summary
# ---------------------------------------------------------------------------


class TestNewsSummary:
    def test_domain_order_and_caps(self) -> None:
        news = tuple(
            _article(f"财经{i}", importance=i) for i in range(8)
        ) + (
            _article("时政", domain="political", source="cctv", importance=9),
        )
        summaries = summarize_news(news)
        domains = [s.domain for s in summaries]
        assert domains == ["financial", "political", "global"]
        fin = summaries[0]
        assert fin.article_count == 8
        assert len(fin.top_headlines) == 5
        # Sorted by importance desc — top headline is 财经7.
        assert "财经7" in fin.top_headlines[0]

    def test_headline_truncation(self) -> None:
        long_title = "长" * 200
        (fin, _, _) = summarize_news((_article(long_title),))
        assert len(fin.top_headlines[0]) < 120

    def test_importance_tie_prefers_newest(self) -> None:
        # codex O-001 P2: crawler articles often share importance 0 —
        # the per-domain cap must keep the NEWEST headlines, not the
        # oldest five.
        articles = tuple(
            NewsArticle(
                title=f"新闻{i}",
                content="正文",
                source="eastmoney",
                url=f"https://example.com/{i}",
                publish_time=datetime(2026, 6, 12, 9, i, tzinfo=UTC),
                importance_score=0,
            )
            for i in range(8)
        )
        (fin, _, _) = summarize_news(articles)
        assert "新闻7" in fin.top_headlines[0]
        assert all("新闻0" not in h and "新闻1" not in h for h in fin.top_headlines)

    def test_vol_20d_uses_twenty_returns(self) -> None:
        # codex O-001 P2: a spike on the FIRST day of the 20-day window
        # must move vol_20d (19-return window would miss it).
        flat = tuple(100.0 for _ in range(21))
        spike = (100.0, 120.0) + tuple(120.0 for _ in range(19))
        vol_flat = compute_index_trend(
            IndexBar(code="X", name="x", closes=flat)
        ).vol_20d
        vol_spike = compute_index_trend(
            IndexBar(code="X", name="x", closes=spike)
        ).vol_20d
        assert vol_flat == 0.0
        assert vol_spike is not None and vol_spike > 0


# ---------------------------------------------------------------------------
# Digest assembly + rendering
# ---------------------------------------------------------------------------


def _digest() -> Any:
    bars = (
        IndexBar(
            code="000300.SH",
            name="沪深300",
            closes=tuple(float(100 + i) for i in range(30)),
        ),
    )
    news = (
        _article("芯片利好", importance=8),
        _article("国际局势", domain="global", source="global_em", importance=6),
    )
    return build_info_digest(
        trade_date=TRADE_DATE,
        index_bars=bars,
        daily_rows=_rows(),
        industry_by_code=INDUSTRY,
        news=news,
        related_sectors=(("半导体", ("消费电子", "光伏")),),
    )


class TestDigest:
    def test_invalid_trade_date_raises(self) -> None:
        with pytest.raises(ValueError):
            build_info_digest(
                trade_date="20260612",
                index_bars=(),
                daily_rows=(),
                industry_by_code={},
                news=(),
            )

    def test_render_contains_all_sections(self) -> None:
        text = render_digest_text(_digest())
        for marker in (
            "市场信息汇总",
            TRADE_DATE,
            "大盘指数与趋势",
            "市场情绪",
            "板块热度",
            "关联板块",
            "多域新闻",
            "沪深300",
            "半导体",
            "消费电子",
        ):
            assert marker in text

    def test_render_deterministic(self) -> None:
        assert render_digest_text(_digest()) == render_digest_text(_digest())

    def test_empty_related_sectors_graceful(self) -> None:
        digest = build_info_digest(
            trade_date=TRADE_DATE,
            index_bars=(),
            daily_rows=(),
            industry_by_code={},
            news=(),
        )
        text = render_digest_text(digest)
        assert "关联板块" in text  # section present, marked无数据

    def test_market_and_news_sections_partition(self) -> None:
        digest = _digest()
        market = render_market_sections(digest)
        news = render_news_section(digest)
        assert "板块热度" in market and "多域新闻" not in market
        assert "多域新闻" in news and "板块热度" not in news


# ---------------------------------------------------------------------------
# Evidence builders + writer
# ---------------------------------------------------------------------------


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.fail_next: Exception | None = None

    async def insert_one(self, doc: dict[str, Any]) -> None:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        for existing in self.docs:
            if existing["evidence_id"] == doc["evidence_id"]:
                raise RuntimeError("E11000 duplicate key on evidence_id")
        self.docs.append(doc)


class _FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


@dataclass
class _FakeMongo:
    _db: _FakeDB = field(default_factory=_FakeDB)


class TestEvidence:
    def test_ids_locked_format(self) -> None:
        market_id = make_market_digest_evidence_id(TRADE_DATE)
        news_id = make_news_digest_evidence_id(TRADE_DATE)
        assert market_id == "MARKET-DIGEST-20260612"
        assert news_id == "NEWS-DIGEST-20260612"
        for eid in (market_id, news_id):
            validate_evidence_id(eid)
        assert parse_evidence_prefix(market_id).value == "MARKET"
        assert parse_evidence_prefix(news_id).value == "NEWS"

    def test_builders_carry_section_content(self) -> None:
        digest = _digest()
        market_ev = build_market_digest_evidence(digest)
        news_ev = build_news_digest_evidence(digest)
        assert "板块热度" in market_ev.content
        assert "多域新闻" in news_ev.content
        assert market_ev.trade_date == TRADE_DATE

    @pytest.mark.asyncio
    async def test_writer_happy_path_and_idempotent(self) -> None:
        mongo = _FakeMongo()
        writer = InfoDigestEvidenceWriter(mongo)  # type: ignore[arg-type]
        ev = build_market_digest_evidence(_digest())
        assert await writer.write(ev) is True
        # Re-run same trade_date → duplicate id → False, no second row.
        assert await writer.write(ev) is False
        coll = mongo._db["evidence_collection"]
        assert len(coll.docs) == 1

    @pytest.mark.asyncio
    async def test_writer_rejects_foreign_prefix(self) -> None:
        mongo = _FakeMongo()
        writer = InfoDigestEvidenceWriter(mongo)  # type: ignore[arg-type]
        ev = build_market_digest_evidence(_digest())
        bad = type(ev)(
            evidence_id="MIROFISH-DIGEST-20260612",
            kind=ev.kind,
            content=ev.content,
            trade_date=ev.trade_date,
        )
        with pytest.raises(InfoDigestEvidenceError):
            await writer.write(bad)

    @pytest.mark.asyncio
    async def test_writer_generic_failure_returns_false(self) -> None:
        mongo = _FakeMongo()
        coll = mongo._db["evidence_collection"]
        coll.fail_next = OSError("mongo down")
        writer = InfoDigestEvidenceWriter(mongo)  # type: ignore[arg-type]
        ev = build_news_digest_evidence(_digest())
        assert await writer.write(ev) is False
        assert coll.docs == []


# ---------------------------------------------------------------------------
# Zero-LLM module contract (O-001 acceptance)
# ---------------------------------------------------------------------------


class TestZeroLlmContract:
    @pytest.mark.parametrize(
        "module_file", ["info_digest.py", "digest_evidence.py"]
    )
    def test_no_llm_imports(self, module_file: str) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "mirofish"
            / module_file
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        banned = ("backend.llm", "backend.agents", "backend.agents_team")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(
                    name == b or name.startswith(b + ".") for b in banned
                ), f"{module_file} imports banned module {name}"
