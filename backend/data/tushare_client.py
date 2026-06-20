"""Tushare Pro full-market client — official Python SDK only (K-001).

Governing decisions
-------------------
* **R0 §6** — Tushare is reached through the *official* ``tushare``
  Python SDK: ``pro = ts.pro_api(token)`` then ``pro.daily`` /
  ``pro.daily_basic`` / ``pro.fina_indicator_vip`` / ``pro.adj_factor``
  / ``pro.index_daily`` / ``pro.fund_daily``. The synchronous SDK call
  is wrapped in :func:`asyncio.to_thread` (same pattern as the adata /
  akshare / baostock clients). **MCP servers / agent-skill
  "fetch-at-LLM-inference" modes are forbidden in the runtime data
  path** — they collide with four red lines: PIT reproducibility
  (R0 §3, an ad-hoc LLM fetch can't be snapshotted/replayed),
  LLM↔data isolation (R0 §4 / P0-10), the 0-LLM full-market screen
  (L-002), and the ¥20/day cost ceiling (P1-7 — looping 5000 symbols
  through an LLM tool call is absurd).
* **P0-8-amendment-2026-05-24-tushare-data-source** — Tushare is the
  *new* full-market scan layer; it does **not** replace the existing
  adata/akshare realtime主备 or the multi-domain news sources. The
  ``TUSHARE_TOKEN`` credential is *heterogeneous* (os.environ only,
  never ``.env``, fingerprint-logged, outside the LLM 3 + Feishu 5
  pool — see ``secrets_validator``).

The raw DataFrame returned by each method is handed straight to the
``MarketDataSnapshot`` store (K-002) for byte-exact PIT persistence;
this module performs **no** business logic on the payload.

Egress note: the tushare SDK uses ``requests`` internally. The
QuantMind host has no IPv6 default route (see ipv4-only-egress memo);
``api.tushare.pro`` resolves to IPv4, so SDK calls do not stall. This
client adds no IPv6-publishing endpoints to the data path.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast, runtime_checkable

import pandas as pd
import structlog

log = structlog.get_logger(component="data.tushare_client")

TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
"""Process-env name for the Tushare Pro token. Heterogeneous credential
(P0-8-amendment-2026-05-24): os.environ only, never .env."""

# Tushare argument shapes — validated at the boundary so a malformed
# date never reaches the SDK (and never silently returns an empty
# full-market frame that would corrupt a snapshot's coverage manifest).
_TRADE_DATE_RE = re.compile(r"\A\d{8}\Z")  # YYYYMMDD
_PERIOD_RE = re.compile(r"\A\d{8}\Z")  # report period end, e.g. 20251231
_TS_CODE_RE = re.compile(r"\A\d{6}\.(SH|SZ|BJ)\Z")  # e.g. 000300.SH

# A single un-paginated *_vip statement call is silently capped by Tushare at a
# per-endpoint row limit (live 2026-06-19: cashflow_vip 6400, balancesheet_vip
# 7000, income_vip 9000), dropping every code past the cap. The statements carry
# MANY rows per code (one per report_type / ann_date filing), so a busy period
# overflows the cap and truncates the universe — corrupting the survivorship
# denominator (PIT completeness is a red line). Paging with an explicit
# limit+offset UNDER the smallest cap retrieves the complete period; pages are
# concatenated in offset order (Tushare returns a stable order per period), so
# the assembled frame is deterministic and byte-replayable.
_STATEMENT_PAGE_LIMIT = 5000

# report_rc (broker analyst earnings-forecast / rating, R4-1) is a sparse STREAM,
# not a daily snapshot: a single report_date returns ~150-900 rows (cap-immune),
# but a multi-month date-RANGE query is silently capped at 5000 rows/call (probed
# 2026-06-20: limit=8000 still returns 5000), so a range pull MUST paginate. The
# page limit is kept STRICTLY BELOW the observed 5000 cap (3000 = the doc-stated
# cap) so a full page never equals the cap — otherwise the short-page stop could
# misfire and silently truncate (the R3-1 *_vip lesson, generalised).
REPORT_RC_PAGE_LIMIT = 3000
# Explicit field pin so a re-pull is byte-stable AND includes ``create_time`` (the
# insert/update timestamp Tushare omits from report_rc's default field set) — it
# lets a PIT factor build drop backfilled rows (create_time >> report_date). These
# are the 21 default columns + create_time. NB ``tp`` = 利润总额/total-profit (万元),
# NOT the target price; the target price is ``min_price`` (max_price ~always empty).
REPORT_RC_FIELDS = (
    "ts_code,name,report_date,report_title,report_type,classify,org_name,"
    "author_name,quarter,op_rt,op_pr,tp,np,eps,pe,rd,roe,ev_ebitda,rating,"
    "max_price,min_price,create_time"
)


class TushareConfigError(RuntimeError):
    """Raised when the client cannot be constructed.

    Causes: ``TUSHARE_TOKEN`` absent (and no pre-built ``pro`` injected),
    or the ``tushare`` package is not installed.
    """


class TushareFetchError(RuntimeError):
    """Raised when an SDK call fails and no fallback is configured.

    Fail-closed: a corrupted / empty fetch must surface loudly rather
    than feed a half-empty full-market frame into a PIT snapshot.
    """

    def __init__(self, endpoint: str, params: dict[str, Any]) -> None:
        super().__init__(
            f"tushare endpoint {endpoint!r} failed (params={params}) "
            "and no fallback provider is configured"
        )
        self.endpoint = endpoint
        self.params = params


@runtime_checkable
class TusharePro(Protocol):
    """Minimal protocol satisfied by ``tushare.pro_api(token)``.

    Only the endpoints K-001 needs are declared; the real object has
    many more. Declaring it as a Protocol lets unit tests inject a fake
    without a live token or network.
    """

    def daily(self, **kwargs: Any) -> pd.DataFrame: ...
    def daily_basic(self, **kwargs: Any) -> pd.DataFrame: ...
    def adj_factor(self, **kwargs: Any) -> pd.DataFrame: ...
    def fina_indicator_vip(self, **kwargs: Any) -> pd.DataFrame: ...
    def income_vip(self, **kwargs: Any) -> pd.DataFrame: ...
    def cashflow_vip(self, **kwargs: Any) -> pd.DataFrame: ...
    def balancesheet_vip(self, **kwargs: Any) -> pd.DataFrame: ...
    def report_rc(self, **kwargs: Any) -> pd.DataFrame: ...
    def namechange(self, **kwargs: Any) -> pd.DataFrame: ...
    def index_daily(self, **kwargs: Any) -> pd.DataFrame: ...
    def index_weight(self, **kwargs: Any) -> pd.DataFrame: ...
    def index_member_all(self, **kwargs: Any) -> pd.DataFrame: ...
    def fund_daily(self, **kwargs: Any) -> pd.DataFrame: ...
    def stock_basic(self, **kwargs: Any) -> pd.DataFrame: ...


@runtime_checkable
class TushareFallback(Protocol):
    """Duck-typed fallback provider (akshare/baostock/adata wrapper).

    The fallback semantics differ from Tushare (per-stock vs
    full-market, no bit-exact guarantee — P0-8-amendment §2.1); it is a
    best-effort degrade path, not a snapshot-grade source.
    """

    async def fetch(self, endpoint: str, params: dict[str, Any]) -> pd.DataFrame: ...


def _fingerprint(value: str) -> str:
    """SHA256[:8] hex — never logs plaintext (P1-6 §1.2)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


class TushareClient:
    """Async wrapper over the official tushare Pro SDK.

    Args:
        pro: A pre-built ``ts.pro_api(token)`` object. Injected by tests;
            in production it is built lazily from ``token``.
        token: Explicit token. Falls back to ``os.environ[TUSHARE_TOKEN]``.
            Required when ``pro`` is not supplied.
        fallback: Optional degrade provider invoked when an SDK call
            raises. When absent, a failed call raises
            :class:`TushareFetchError` (fail-closed).
    """

    def __init__(
        self,
        pro: TusharePro | None = None,
        *,
        token: str | None = None,
        fallback: TushareFallback | None = None,
    ) -> None:
        resolved = (token or "").strip()
        if not resolved:
            import os

            resolved = os.environ.get(TUSHARE_TOKEN_ENV, "").strip()

        self._token_fingerprint = _fingerprint(resolved) if resolved else ""
        self._fallback = fallback

        if pro is not None:
            self._pro: TusharePro = pro
        else:
            if not resolved:
                raise TushareConfigError(
                    f"{TUSHARE_TOKEN_ENV} is not set and no pro client was "
                    "injected — export the token in ~/.bashrc"
                )
            self._pro = self._build_pro(resolved)

        log.info(
            "tushare_client_init",
            token_fingerprint=self._token_fingerprint or "(injected-pro)",
            fallback_enabled=fallback is not None,
        )

    @staticmethod
    def _build_pro(token: str) -> TusharePro:
        """Construct the real SDK client; isolate the import so tests and
        non-data code never pull tushare in transitively."""
        try:
            import tushare as ts
        except ImportError as exc:  # pragma: no cover - install-time guard
            raise TushareConfigError(
                "tushare package not installed; add tushare>=1.4 (R0 §6)"
            ) from exc
        # tushare is untyped (no py.typed) → pro_api returns Any; the SDK object
        # structurally satisfies the TusharePro Protocol (asserted by the live
        # endpoint tests), so cast at this isolated boundary keeps strict mypy green.
        return cast(TusharePro, ts.pro_api(token))

    @property
    def token_fingerprint(self) -> str:
        """SHA256[:8] of the token, or ``""`` when a pro was injected."""
        return self._token_fingerprint

    # -- validation ----------------------------------------------------

    @staticmethod
    def _check_trade_date(trade_date: str) -> None:
        if not _TRADE_DATE_RE.match(trade_date):
            raise ValueError(f"trade_date {trade_date!r} must be YYYYMMDD (8 digits)")

    @staticmethod
    def _check_period(period: str) -> None:
        if not _PERIOD_RE.match(period):
            raise ValueError(f"period {period!r} must be a report-period end YYYYMMDD")

    @staticmethod
    def _check_ts_code(ts_code: str) -> None:
        if not _TS_CODE_RE.match(ts_code):
            raise ValueError(
                f"ts_code {ts_code!r} must look like 000300.SH / 600519.SH "
                "/ 000001.SZ / 430047.BJ"
            )

    # -- fetch ---------------------------------------------------------

    async def _fetch(self, endpoint: str, params: dict[str, Any]) -> pd.DataFrame:
        """Call ``pro.<endpoint>(**params)`` off-thread; degrade or fail-close."""
        fn = getattr(self._pro, endpoint)
        try:
            return await asyncio.to_thread(lambda: fn(**params))
        except Exception as exc:  # noqa: BLE001 - degrade path is deliberate
            if self._fallback is None:
                log.error("tushare_fetch_failed", endpoint=endpoint, error=str(exc))
                raise TushareFetchError(endpoint, params) from exc
            log.warning("tushare_fetch_fallback", endpoint=endpoint, error=str(exc))
            return await self._fallback.fetch(endpoint, params)

    async def _fetch_paginated(
        self,
        endpoint: str,
        params: dict[str, Any],
        *,
        page_limit: int = _STATEMENT_PAGE_LIMIT,
        throttle: Callable[[], Awaitable[None]] | None = None,
    ) -> pd.DataFrame:
        """Page ``pro.<endpoint>`` with ``limit``+``offset`` until a short page.

        Each page reuses :meth:`_fetch` (so the degrade/fail-closed and fallback
        path is identical to a single call). Paging stops on the first page with
        fewer than ``page_limit`` rows (the last page) or an exact-boundary empty
        page. An entirely empty period returns the first (column-bearing) frame so
        the stored CSV stays replayable. See :data:`_STATEMENT_PAGE_LIMIT` for why
        the *_vip statements need this and other full-market pulls do not.

        ``throttle`` (when given) is awaited once before EACH page, so a paginated
        pull consumes exactly one rate-limit token per real SDK call (a single
        per-period token would under-count multi-page periods and overrun the
        vendor cap). The caller must therefore not also throttle this whole call.
        """
        frames: list[pd.DataFrame] = []
        offset = 0
        while True:
            if throttle is not None:
                await throttle()
            page = await self._fetch(
                endpoint, {**params, "limit": page_limit, "offset": offset}
            )
            if page is None or (page.empty and frames):
                break  # end reached (exact page boundary returns an empty tail)
            frames.append(page)
            if len(page) < page_limit:
                break
            offset += page_limit
        if not frames:
            return pd.DataFrame()
        return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)

    # -- full-market single-pull endpoints -----------------------------

    async def daily(self, trade_date: str) -> pd.DataFrame:
        """Full-market daily OHLCV for a trade date (~5400 rows, one call)."""
        self._check_trade_date(trade_date)
        return await self._fetch("daily", {"trade_date": trade_date})

    async def daily_basic(self, trade_date: str) -> pd.DataFrame:
        """Full-market daily valuation metrics (pe/pb/turnover/…)."""
        self._check_trade_date(trade_date)
        return await self._fetch("daily_basic", {"trade_date": trade_date})

    async def adj_factor(self, trade_date: str) -> pd.DataFrame:
        """Full-market adjustment factors for the trade date (K-004 pin)."""
        self._check_trade_date(trade_date)
        return await self._fetch("adj_factor", {"trade_date": trade_date})

    async def fina_indicator_vip(self, period: str) -> pd.DataFrame:
        """Full-market fundamentals for a report period (~7194 rows, 5000档 vip)."""
        self._check_period(period)
        return await self._fetch("fina_indicator_vip", {"period": period})

    async def income_vip(
        self, period: str, *, throttle: Callable[[], Awaitable[None]] | None = None
    ) -> pd.DataFrame:
        """Full-market income statement for a report period (~6700 rows, vip).

        Round-3 accruals input (``n_income`` = net profit). Multiple
        ``report_type`` rows per ``(ts_code, end_date)`` exist; the PIT
        consolidated-report (``report_type='1'``) selection + ann_date vintage
        gating live in the R3-2 statements reader, not here. Paginated so the
        per-call row cap never truncates the universe (see
        :data:`_STATEMENT_PAGE_LIMIT`); ``throttle`` is awaited per page.
        """
        self._check_period(period)
        return await self._fetch_paginated(
            "income_vip", {"period": period}, throttle=throttle
        )

    async def cashflow_vip(
        self, period: str, *, throttle: Callable[[], Awaitable[None]] | None = None
    ) -> pd.DataFrame:
        """Full-market cash-flow statement for a report period (vip, paginated).

        Round-3 accruals input (``n_cashflow_act`` = operating cash flow). This
        statement has the most rows per code, so a single call is capped at 6400
        rows and drops ~2400 codes per busy period — paging is mandatory (see
        :data:`_STATEMENT_PAGE_LIMIT`); ``throttle`` is awaited per page.
        """
        self._check_period(period)
        return await self._fetch_paginated(
            "cashflow_vip", {"period": period}, throttle=throttle
        )

    async def balancesheet_vip(
        self, period: str, *, throttle: Callable[[], Awaitable[None]] | None = None
    ) -> pd.DataFrame:
        """Full-market balance sheet for a report period (vip, paginated).

        Round-3 accruals + asset-growth input (``total_assets``, a stock — no
        YTD differencing, but still ann_date-gated PIT). Paginated so the
        per-call row cap never truncates the universe (see
        :data:`_STATEMENT_PAGE_LIMIT`); ``throttle`` is awaited per page.
        """
        self._check_period(period)
        return await self._fetch_paginated(
            "balancesheet_vip", {"period": period}, throttle=throttle
        )

    async def report_rc(
        self,
        *,
        report_date: str = "",
        start_date: str = "",
        end_date: str = "",
        fields: str = REPORT_RC_FIELDS,
        throttle: Callable[[], Awaitable[None]] | None = None,
    ) -> pd.DataFrame:
        """Broker analyst earnings-forecast / rating reports (``report_rc``, R4-1).

        The round-4 analyst-revision alpha source. Query EITHER a single
        ``report_date`` (a publication day — cap-immune, ≤~900 rows, one call) OR a
        ``start_date``/``end_date`` range (paginated — a range is silently capped at
        5000 rows/call, see :data:`REPORT_RC_PAGE_LIMIT`). ``report_date`` is the PIT
        availability date (the report loads that night → tradable D+1).

        Exactly one of ``report_date`` OR the ``start_date``/``end_date`` range must
        be given (a mix is ambiguous and rejected fail-closed); a range requires both
        ends and ``start_date <= end_date``. ``fields`` is pinned to
        :data:`REPORT_RC_FIELDS` (incl. ``create_time``) for byte-stable replayable
        pulls. ``throttle`` (range mode) is awaited once per page.

        NB: ``tp`` is 利润总额/total-profit (万元), NOT the target price — the target
        price is ``min_price`` (``max_price`` is almost always empty). Multiple rows
        per (ts_code, report_date) are legitimate (one per forecast fiscal year ×
        broker); de-dup/aggregation is a factor-build concern, not done here.
        """
        is_range = bool(start_date or end_date)
        if bool(report_date) == is_range:
            raise ValueError(
                "report_rc needs exactly one of report_date OR a "
                "start_date/end_date range (got both or neither)"
            )
        if report_date:
            self._check_trade_date(report_date)
            return await self._fetch(
                "report_rc", {"report_date": report_date, "fields": fields}
            )
        if not (start_date and end_date):
            raise ValueError("report_rc range needs both start_date and end_date")
        self._check_trade_date(start_date)
        self._check_trade_date(end_date)
        if start_date > end_date:
            raise ValueError(
                f"report_rc start_date {start_date} > end_date {end_date}"
            )
        return await self._fetch_paginated(
            "report_rc",
            {"start_date": start_date, "end_date": end_date, "fields": fields},
            page_limit=REPORT_RC_PAGE_LIMIT,
            throttle=throttle,
        )

    async def namechange(
        self, *, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        """Historical share name changes (``ts_code`` / ``name`` / ``start_date`` /
        ``end_date`` / ``change_reason``) — the PIT ST-flag source (R3-1).

        A name's ``start_date`` / ``end_date`` give the interval it was in
        effect, so a backtest can reconstruct the point-in-time name (and the
        ``*ST`` / ``ST`` / 退 prefix) of any code on any date. The offline ingest
        paginates by the change ``start_date`` *year* (each year is far under the
        per-call row cap) for a complete timeline; an empty year is legitimate
        (no name changes that year), so the ingest does NOT require non-empty.
        Pass neither date for a single best-effort full pull.
        """
        params: dict[str, Any] = {}
        if start_date:
            self._check_trade_date(start_date)
            params["start_date"] = start_date
        if end_date:
            self._check_trade_date(end_date)
            params["end_date"] = end_date
        return await self._fetch("namechange", params)

    async def index_daily(
        self, ts_code: str, *, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        """Index OHLCV by ts_code (e.g. 000300.SH 沪深300)."""
        self._check_ts_code(ts_code)
        params: dict[str, Any] = {"ts_code": ts_code}
        if start_date:
            self._check_trade_date(start_date)
            params["start_date"] = start_date
        if end_date:
            self._check_trade_date(end_date)
            params["end_date"] = end_date
        return await self._fetch("index_daily", params)

    async def index_weight(
        self,
        index_code: str,
        *,
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Index constituent weights for a date or date range (PIT).

        Round-2 benchmark-relative input: ``index_weight('000300.SH',
        start_date=m0, end_date=m1)`` returns the CSI300 ``con_code`` /
        ``weight`` rows *published* in ``[m0, m1]`` (weights are released
        ~monthly on a specific publish date that need not be a month-end, so a
        single arbitrary ``trade_date`` can come back empty — query by month
        range and persist whatever publish date lands inside). The caller
        reads it as-of with an availability lag (round-2 plan §4.1) so a weight
        published on ``d`` is never used to trade on ``d``.

        Exactly one of ``trade_date`` or the ``start_date`` / ``end_date`` range
        must be given (a mix is ambiguous and rejected fail-closed); a range
        requires both ends and ``start_date <= end_date``.
        """
        self._check_ts_code(index_code)
        is_range = bool(start_date or end_date)
        if bool(trade_date) == is_range:
            raise ValueError(
                "index_weight needs exactly one of trade_date OR a "
                "start_date/end_date range (got both or neither)"
            )
        params: dict[str, Any] = {"index_code": index_code}
        if trade_date:
            self._check_trade_date(trade_date)
            params["trade_date"] = trade_date
        else:
            if not (start_date and end_date):
                raise ValueError(
                    "index_weight range needs both start_date and end_date"
                )
            self._check_trade_date(start_date)
            self._check_trade_date(end_date)
            if start_date > end_date:
                raise ValueError(
                    f"index_weight start_date {start_date} > end_date {end_date}"
                )
            params["start_date"] = start_date
            params["end_date"] = end_date
        return await self._fetch("index_weight", params)

    async def index_member_all(self) -> pd.DataFrame:
        """申万 industry membership table with ``in_date`` / ``out_date`` (PIT).

        One full pull of the whole 申万 (SW) classification: each row carries a
        code's industry codes plus the dates it entered / left that industry, so
        a backtest can reconstruct the **point-in-time** industry of any code on
        any date (``in_date <= d < out_date``) — the survivorship-/look-ahead-
        safe alternative to ``stock_basic.industry`` (current-only). Not
        date-specific; the caller keys the snapshot by the as-of pull date.
        """
        return await self._fetch("index_member_all", {})

    async def fund_daily(self, trade_date: str) -> pd.DataFrame:
        """Full-market ETF/LOF daily OHLCV for a trade date."""
        self._check_trade_date(trade_date)
        return await self._fetch("fund_daily", {"trade_date": trade_date})

    async def trade_cal(
        self,
        *,
        start_date: str,
        end_date: str,
        exchange: str = "SSE",
    ) -> pd.DataFrame:
        """Exchange trading calendar for a date range (``cal_date`` / ``is_open``).

        Calendar **enumeration aid** for the offline bulk historical ingest
        (P0-8-amendment-2026-06-14): it lets the job iterate only real
        trading days instead of calling the full-market ``daily`` endpoint on
        every weekend/holiday (~190 wasted, rate-limited calls over 11 years)
        and avoids conflating a holiday-empty frame with a fetch failure. This
        is the Tushare *official SDK* (not the akshare 节假日 API barred by
        P0-6 §1.4), and it is offline batch only — the runtime trading
        calendar stays the static ``config/holidays.yaml`` (A-007 hot-reload
        disabled). ``trade_cal`` is a calendar lookup, not one of the
        persisted-snapshot data endpoints (daily / adj_factor / daily_basic /
        stock_basic / index_daily / fund_daily, amendment §2.1).
        """
        self._check_trade_date(start_date)
        self._check_trade_date(end_date)
        return await self._fetch(
            "trade_cal",
            {
                "exchange": exchange,
                "start_date": start_date,
                "end_date": end_date,
                "is_open": "1",
            },
        )

    async def stock_basic(
        self,
        *,
        list_status: str = "L",
        fields: str = "ts_code,name,list_date",
    ) -> pd.DataFrame:
        """Listed-stock reference table (``ts_code`` / ``name`` / ``list_date``).

        Not date-specific — it is the current listing roster. The Line-1
        frame assembler (U-B1) joins it to per-date ``daily`` rows for the
        display name and the listing-age exclusion (IPO / sub-new). Tushare
        ``stock_basic`` is rate-limited to ~50 calls/min, so the assembler
        fetches it once per run (and reuses the PIT snapshot on re-runs).
        """
        return await self._fetch(
            "stock_basic", {"list_status": list_status, "fields": fields}
        )


__all__ = [
    "TUSHARE_TOKEN_ENV",
    "TushareClient",
    "TushareConfigError",
    "TushareFallback",
    "TushareFetchError",
    "TusharePro",
]
