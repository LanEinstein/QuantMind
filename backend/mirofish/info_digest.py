"""O-001 deterministic information digest (zero LLM).

Aggregates multi-domain 5-source news + index trends/history + full-market
sector heat (derived from per-date daily rows joined to an industry map) +
deterministic market-sentiment proxies + KG-derived related sectors into
one order-stable Chinese document. The rendered document is the MiroFish
sector-forecast input (O-002) and one of the off-market debate inputs
(O-004).

Red lines honored by construction:

* **Zero LLM** — pure computation over injected data; no network, no
  Mongo, no clock reads (``trade_date`` is an explicit input). The
  zero-LLM AST contract test locks this.
* **Evidence-only** — persistence goes through
  :mod:`backend.mirofish.digest_evidence` with the locked ``NEWS-`` /
  ``MARKET-`` prefixes (P0-8 §1.6.2); this module never touches Mongo.
* **PIT determinism** — same inputs produce a bit-exact document. All
  collections are sorted with explicit tie-breakers and floats are
  rounded once at the boundary.

The "sentiment" section is a *deterministic proxy* (market breadth +
limit-move counts + cross-domain news echo) — interpretation of these
numbers is left to the O-002 LLM layer; nothing here is a decision field.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import structlog

from backend.models.market import NewsArticle

log = structlog.get_logger(component="mirofish.info_digest")

# Sector heat needs a minimal cross-section per sector — a one-stock
# "sector" is just that stock's day, not sector heat.
MIN_SECTOR_CONSTITUENTS = 3

# Document caps — keep the rendered digest bounded so the downstream LLM
# prompt (O-002) and the debate context (O-004) stay cheap.
TOP_SECTOR_COUNT = 10
BOTTOM_SECTOR_COUNT = 5
MAX_HEADLINES_PER_DOMAIN = 5
HEADLINE_MAX_CHARS = 80

# News with importance_score >= this counts toward the high-importance
# sentiment proxy (matches the 0-10 scale of NewsArticle).
HIGH_IMPORTANCE_THRESHOLD = 7

# Limit-move proxy thresholds (pct_chg, percent units). Approximation by
# code prefix only — this feeds a sentiment *count*, never a trading
# decision (price-limit trading rules live in RiskEngine / MockBroker).
_WIDE_LIMIT_PREFIXES = ("30", "68")  # ChiNext / STAR ±20%
_WIDE_LIMIT_THRESHOLD = 19.8
_MAIN_LIMIT_THRESHOLD = 9.8

_TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Fixed domain rendering order (P0-8 multi-domain footprint).
_DOMAIN_ORDER: tuple[str, ...] = ("financial", "political", "global")
_DOMAIN_LABELS: Mapping[str, str] = {
    "financial": "财经",
    "political": "时政",
    "global": "全球",
}

_PUNCT_RE = re.compile(r"[\s,。,.;;::!!??“”\"'《》()()\[\]【】、·-]+")


# ---------------------------------------------------------------------------
# Input / output structures (all frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexBar:
    """Trailing daily closes for one index, oldest → newest."""

    code: str
    name: str
    closes: tuple[float, ...]


@dataclass(frozen=True)
class DailyStockRow:
    """One full-market daily row (subset of Tushare ``daily``)."""

    code: str  # ts_code, e.g. ``600519.SH``
    pct_chg: float  # percent, e.g. ``3.25``
    amount: float  # traded amount (relative weight only; unit-agnostic)


@dataclass(frozen=True)
class IndexTrend:
    """Deterministic per-index trend metrics; ``None`` = not derivable."""

    code: str
    name: str
    last_close: float | None
    ret_1d: float | None
    ret_5d: float | None
    ret_20d: float | None
    above_ma20: bool | None
    vol_20d: float | None  # stdev of last-20 daily returns, percent


@dataclass(frozen=True)
class SectorHeat:
    """Cross-sectional heat metrics for one industry sector."""

    sector: str
    stock_count: int
    advancers: int
    decliners: int
    mean_pct_chg: float
    amount_share: float  # of the grouped (sector-mapped) total
    heat_score: float  # composite percentile blend in [0, 1]


@dataclass(frozen=True)
class SentimentIndicators:
    """Deterministic market-sentiment proxies (counts only)."""

    advancers: int
    decliners: int
    flat: int
    limit_up_count: int
    limit_down_count: int
    cross_domain_echo_count: int
    high_importance_news_count: int


@dataclass(frozen=True)
class NewsDomainSummary:
    """Per-domain news roll-up for the digest document."""

    domain: str
    article_count: int
    top_headlines: tuple[str, ...]


@dataclass(frozen=True)
class InfoDigest:
    """The assembled digest — input to rendering + evidence builders."""

    trade_date: str
    index_trends: tuple[IndexTrend, ...]
    sector_heat: tuple[SectorHeat, ...]
    related_sectors: tuple[tuple[str, tuple[str, ...]], ...]
    sentiment: SentimentIndicators
    news: tuple[NewsDomainSummary, ...]

    @property
    def sector_names(self) -> tuple[str, ...]:
        """Sector vocabulary of this digest (O-002 forecast must stay in it)."""
        return tuple(h.sector for h in self.sector_heat)


# ---------------------------------------------------------------------------
# Index trend
# ---------------------------------------------------------------------------


def _trailing_return(closes: Sequence[float], days: int) -> float | None:
    """Percent return over ``days`` trading days; ``None`` when undefined."""
    if len(closes) < days + 1:
        return None
    base = closes[-(days + 1)]
    last = closes[-1]
    if base <= 0 or last <= 0:
        return None
    return (last / base - 1.0) * 100.0


def compute_index_trend(bar: IndexBar) -> IndexTrend:
    """Derive deterministic trend metrics for one index.

    Any metric whose window contains a non-positive close degrades to
    ``None`` (fail-closed metric, never ``inf``/garbage).
    """
    closes = bar.closes
    last_close = closes[-1] if closes else None

    above_ma20: bool | None = None
    vol_20d: float | None = None
    if len(closes) >= 20 and all(c > 0 for c in closes[-20:]):
        ma20 = sum(closes[-20:]) / 20.0
        above_ma20 = bool(closes[-1] > ma20)
    if len(closes) >= 21 and all(c > 0 for c in closes[-21:]):
        rets = [
            (closes[i] / closes[i - 1] - 1.0) * 100.0
            for i in range(len(closes) - 20, len(closes))
        ]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        vol_20d = round(math.sqrt(var), 6)

    return IndexTrend(
        code=bar.code,
        name=bar.name,
        last_close=last_close,
        ret_1d=_round_opt(_trailing_return(closes, 1)),
        ret_5d=_round_opt(_trailing_return(closes, 5)),
        ret_20d=_round_opt(_trailing_return(closes, 20)),
        above_ma20=above_ma20,
        vol_20d=vol_20d,
    )


def _round_opt(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


# ---------------------------------------------------------------------------
# Sector heat
# ---------------------------------------------------------------------------


def _percentile_rank(values: Sequence[float], value: float) -> float:
    """Fraction of *other* values strictly below ``value`` (ties share)."""
    n = len(values)
    if n <= 1:
        return 0.5
    below = sum(1 for v in values if v < value)
    return below / (n - 1)


def compute_sector_heat(
    rows: Sequence[DailyStockRow],
    industry_by_code: Mapping[str, str],
) -> tuple[SectorHeat, ...]:
    """Aggregate per-date daily rows into ranked sector heat.

    Codes missing from ``industry_by_code`` (or mapped to an empty
    sector) are skipped; sectors with fewer than
    :data:`MIN_SECTOR_CONSTITUENTS` constituents are dropped. The
    composite ``heat_score`` blends cross-sectional percentile ranks of
    mean return (0.5), amount share (0.3) and advancing breadth (0.2) —
    locked constants, runtime-immutable.
    """
    groups: dict[str, list[DailyStockRow]] = {}
    for row in rows:
        sector = industry_by_code.get(row.code, "").strip()
        if not sector:
            continue
        groups.setdefault(sector, []).append(row)

    kept = {
        sector: members
        for sector, members in groups.items()
        if len(members) >= MIN_SECTOR_CONSTITUENTS
    }
    if not kept:
        return ()

    total_amount = sum(r.amount for members in kept.values() for r in members)
    raw: list[tuple[str, int, int, int, float, float, float]] = []
    for sector in sorted(kept):
        members = kept[sector]
        mean_pct = sum(r.pct_chg for r in members) / len(members)
        advancers = sum(1 for r in members if r.pct_chg > 0)
        decliners = sum(1 for r in members if r.pct_chg < 0)
        amount = sum(r.amount for r in members)
        share = amount / total_amount if total_amount > 0 else 0.0
        breadth = advancers / len(members)
        raw.append(
            (sector, len(members), advancers, decliners, mean_pct, share, breadth)
        )

    means = [r[4] for r in raw]
    shares = [r[5] for r in raw]
    breadths = [r[6] for r in raw]

    heat = [
        SectorHeat(
            sector=sector,
            stock_count=count,
            advancers=adv,
            decliners=dec,
            mean_pct_chg=round(mean_pct, 6),
            amount_share=round(share, 6),
            heat_score=round(
                0.5 * _percentile_rank(means, mean_pct)
                + 0.3 * _percentile_rank(shares, share)
                + 0.2 * _percentile_rank(breadths, breadth),
                6,
            ),
        )
        for sector, count, adv, dec, mean_pct, share, breadth in raw
    ]
    # Hottest first; sector name is the deterministic tie-breaker.
    heat.sort(key=lambda h: (-h.heat_score, h.sector))
    return tuple(heat)


# ---------------------------------------------------------------------------
# Sentiment proxies
# ---------------------------------------------------------------------------


def _limit_threshold(code: str) -> float:
    """Limit-move proxy threshold by code prefix (sentiment count only)."""
    bare = code.split(".", 1)[0]
    if bare.startswith(_WIDE_LIMIT_PREFIXES):
        return _WIDE_LIMIT_THRESHOLD
    return _MAIN_LIMIT_THRESHOLD


def _normalize_title(title: str) -> str:
    return _PUNCT_RE.sub("", title).lower()


def compute_sentiment_indicators(
    rows: Sequence[DailyStockRow],
    news: Sequence[NewsArticle],
) -> SentimentIndicators:
    """Deterministic sentiment proxies from market breadth + news shape.

    Cross-domain echo = one normalized headline appearing in ≥2 distinct
    news domains (the multi-domain duplicate signal P0-8 §1.2 preserves
    on purpose).
    """
    advancers = sum(1 for r in rows if r.pct_chg > 0)
    decliners = sum(1 for r in rows if r.pct_chg < 0)
    flat = len(rows) - advancers - decliners
    limit_up = sum(1 for r in rows if r.pct_chg >= _limit_threshold(r.code))
    limit_down = sum(1 for r in rows if r.pct_chg <= -_limit_threshold(r.code))

    domains_by_title: dict[str, set[str]] = {}
    for article in news:
        key = _normalize_title(article.title)
        if key:
            domains_by_title.setdefault(key, set()).add(str(article.domain))
    echo = sum(1 for domains in domains_by_title.values() if len(domains) >= 2)
    high_importance = sum(
        1
        for article in news
        if article.importance_score >= HIGH_IMPORTANCE_THRESHOLD
    )

    return SentimentIndicators(
        advancers=advancers,
        decliners=decliners,
        flat=flat,
        limit_up_count=limit_up,
        limit_down_count=limit_down,
        cross_domain_echo_count=echo,
        high_importance_news_count=high_importance,
    )


# ---------------------------------------------------------------------------
# News summary
# ---------------------------------------------------------------------------


def summarize_news(
    news: Sequence[NewsArticle],
) -> tuple[NewsDomainSummary, ...]:
    """Per-domain roll-up in the fixed financial/political/global order."""
    summaries: list[NewsDomainSummary] = []
    for domain in _DOMAIN_ORDER:
        articles = [a for a in news if str(a.domain) == domain]
        # Importance first; ties prefer the NEWEST headlines (codex O-001
        # P2 — crawler articles often share importance_score=0, and the
        # cap must not keep the oldest five).
        articles.sort(key=lambda a: a.title)
        articles.sort(key=lambda a: a.publish_time, reverse=True)
        articles.sort(key=lambda a: a.importance_score, reverse=True)
        headlines = tuple(
            f"[{a.importance_score}/10] {a.title[:HEADLINE_MAX_CHARS]}"
            for a in articles[:MAX_HEADLINES_PER_DOMAIN]
        )
        summaries.append(
            NewsDomainSummary(
                domain=domain,
                article_count=len(articles),
                top_headlines=headlines,
            )
        )
    return tuple(summaries)


# ---------------------------------------------------------------------------
# Digest assembly
# ---------------------------------------------------------------------------


def build_info_digest(
    *,
    trade_date: str,
    index_bars: Sequence[IndexBar],
    daily_rows: Sequence[DailyStockRow],
    industry_by_code: Mapping[str, str],
    news: Sequence[NewsArticle],
    related_sectors: Sequence[tuple[str, tuple[str, ...]]] = (),
) -> InfoDigest:
    """Assemble the deterministic digest for one trade date.

    ``related_sectors`` is injected (typically KG industry-chain
    adjacency for the hottest sectors); an empty sequence renders a
    graceful "no graph data" section — the digest never blocks on the
    knowledge graph.
    """
    if not _TRADE_DATE_RE.fullmatch(trade_date):
        raise ValueError(
            f"trade_date {trade_date!r} must be YYYY-MM-DD"
        )
    return InfoDigest(
        trade_date=trade_date,
        index_trends=tuple(compute_index_trend(b) for b in index_bars),
        sector_heat=compute_sector_heat(daily_rows, industry_by_code),
        related_sectors=tuple(
            (sector, tuple(related)) for sector, related in related_sectors
        ),
        sentiment=compute_sentiment_indicators(daily_rows, news),
        news=summarize_news(news),
    )


# ---------------------------------------------------------------------------
# Rendering (order-stable Chinese document)
# ---------------------------------------------------------------------------


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _render_index_section(digest: InfoDigest) -> str:
    lines = ["## 一、大盘指数与趋势(含20日历史)"]
    if not digest.index_trends:
        lines.append("(无指数数据)")
    for t in digest.index_trends:
        close = "N/A" if t.last_close is None else f"{t.last_close:.2f}"
        ma = (
            "N/A"
            if t.above_ma20 is None
            else ("MA20上方" if t.above_ma20 else "MA20下方")
        )
        vol = "N/A" if t.vol_20d is None else f"{t.vol_20d:.2f}%"
        lines.append(
            f"- {t.name}({t.code}): 收盘 {close}, "
            f"1日 {_fmt_pct(t.ret_1d)}, 5日 {_fmt_pct(t.ret_5d)}, "
            f"20日 {_fmt_pct(t.ret_20d)}, {ma}, 20日波动 {vol}"
        )
    return "\n".join(lines)


def _render_sentiment_section(digest: InfoDigest) -> str:
    s = digest.sentiment
    return "\n".join(
        [
            "## 二、市场情绪(确定性代理指标)",
            (
                f"上涨 {s.advancers} 家 / 下跌 {s.decliners} 家 / "
                f"平盘 {s.flat} 家"
            ),
            f"涨停(代理) {s.limit_up_count} 家 / 跌停(代理) {s.limit_down_count} 家",
            (
                f"跨域新闻共振 {s.cross_domain_echo_count} 条 / "
                f"高重要度新闻 {s.high_importance_news_count} 条"
            ),
        ]
    )


def _render_sector_section(digest: InfoDigest) -> str:
    lines = ["## 三、板块热度(全市场行业聚合)"]
    heat = digest.sector_heat
    if not heat:
        lines.append("(无板块数据)")
        return "\n".join(lines)
    top = heat[:TOP_SECTOR_COUNT]
    lines.append(f"### 热度前 {len(top)}")
    for i, h in enumerate(top, start=1):
        lines.append(
            f"{i}. {h.sector}: 均涨跌 {h.mean_pct_chg:+.2f}%, "
            f"广度 {h.advancers}/{h.stock_count}, "
            f"成交占比 {h.amount_share:.1%}, 热度 {h.heat_score:.3f}"
        )
    if len(heat) > TOP_SECTOR_COUNT:
        bottom = heat[-BOTTOM_SECTOR_COUNT:]
        lines.append(f"### 热度后 {len(bottom)}")
        for h in bottom:
            lines.append(
                f"- {h.sector}: 均涨跌 {h.mean_pct_chg:+.2f}%, "
                f"广度 {h.advancers}/{h.stock_count}"
            )
    return "\n".join(lines)


def _render_related_section(digest: InfoDigest) -> str:
    lines = ["## 四、关联板块(知识图谱产业链邻接)"]
    if not digest.related_sectors:
        lines.append("(无图谱数据)")
        return "\n".join(lines)
    for sector, related in digest.related_sectors:
        joined = ", ".join(related) if related else "(无)"
        lines.append(f"- {sector} → {joined}")
    return "\n".join(lines)


def _render_news_section(digest: InfoDigest) -> str:
    lines = ["## 五、多域新闻(财经/时政/全球 5 源)"]
    for summary in digest.news:
        label = _DOMAIN_LABELS.get(summary.domain, summary.domain)
        lines.append(f"### {label}({summary.article_count} 条)")
        if not summary.top_headlines:
            lines.append("(无)")
        lines.extend(f"- {headline}" for headline in summary.top_headlines)
    return "\n".join(lines)


def render_market_sections(digest: InfoDigest) -> str:
    """Sections 一-四 (index/sentiment/sector/related) — MARKET- evidence."""
    return "\n\n".join(
        [
            f"# 市场信息汇总 {digest.trade_date}",
            _render_index_section(digest),
            _render_sentiment_section(digest),
            _render_sector_section(digest),
            _render_related_section(digest),
        ]
    )


def render_news_section(digest: InfoDigest) -> str:
    """Section 五 (multi-domain news) — NEWS- evidence."""
    return "\n\n".join(
        [
            f"# 新闻信息汇总 {digest.trade_date}",
            _render_news_section(digest),
        ]
    )


def render_digest_text(digest: InfoDigest) -> str:
    """The full digest document (MiroFish input + debate off-market input)."""
    return "\n\n".join(
        [
            render_market_sections(digest),
            _render_news_section(digest),
        ]
    )


__all__ = [
    "BOTTOM_SECTOR_COUNT",
    "DailyStockRow",
    "HIGH_IMPORTANCE_THRESHOLD",
    "IndexBar",
    "IndexTrend",
    "InfoDigest",
    "MAX_HEADLINES_PER_DOMAIN",
    "MIN_SECTOR_CONSTITUENTS",
    "NewsDomainSummary",
    "SectorHeat",
    "SentimentIndicators",
    "TOP_SECTOR_COUNT",
    "build_info_digest",
    "compute_index_trend",
    "compute_sector_heat",
    "compute_sentiment_indicators",
    "render_digest_text",
    "render_market_sections",
    "render_news_section",
    "summarize_news",
]
