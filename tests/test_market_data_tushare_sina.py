"""Tushare ``ts.realtime_quote(src=sina)`` backup leg coverage.

P0-8-amendment-2026-05-28: the realtime dual-source ``fallback`` leg switches
from akshare ``stock_zh_a_spot_em()`` (full-market batch, eastmoney throttled)
to Tushare ``realtime_quote(src='sina')`` (single-symbol, sina-sourced,
non-credit-gated). These tests pin the new helpers' contracts so a future
regression cannot silently restore the akshare batch leg or skew the sina
row → :class:`StockQuote` mapping.

Scope (matches amendment §2.3 + post-review hardening):
    * ``_to_tushare_ts_code`` defers to ``classify_board`` — universe-blocked
      prefixes (STAR / 北交 / 可转债 / B-share) fail-closed via
      ``ForbiddenCodeError``; malformed/unknown via ``UnknownCodeError``.
    * ``_tushare_sina_row_to_quote`` uses ``_positive_or_none`` for ``PRICE`` —
      NaN / non-positive raises ``ValueError`` so the dual handler degrades
      to missing-data (not a sham NaN quote).
    * ``_fetch_stock_tushare_sina`` lazy-imports ``tushare`` (file's
      lazy-import discipline mirrors akshare/adata); the test patches
      ``tushare.realtime_quote`` so the mock works without a module-level
      ``ts`` symbol.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd
import pytest

from backend.data.market_data import (
    _fetch_stock_tushare_sina,
    _to_tushare_ts_code,
    _tushare_sina_row_to_quote,
)
from backend.data.stock_metadata import ForbiddenCodeError, UnknownCodeError
from backend.models.market import StockQuote

# ---------------------------------------------------------------------------
# _to_tushare_ts_code (defers to stock_metadata.classify_board)
# ---------------------------------------------------------------------------


class TestToTushareTsCode:
    """6-digit bare code → Tushare ``ts_code`` (``\\d{6}\\.(SH|SZ)``).

    Defers to :func:`backend.data.stock_metadata.classify_board` so the
    universe-blocked prefixes (STAR / 北交 / 可转债 / B-share) raise
    :class:`ForbiddenCodeError` with the project's stable audit reason
    namespace, NOT a generic ValueError. Sharing the prefix table avoids
    the 'two sources of truth' drift the code-review caught (universe
    enforcement done at higher layers must not be re-litigated here).
    """

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("000021", "000021.SZ"),  # 深证主板
            ("002185", "002185.SZ"),  # 深证主板
            ("300750", "300750.SZ"),  # 创业板
            ("600000", "600000.SH"),  # 沪市主板
            ("601318", "601318.SH"),  # 沪市主板
            ("510300", "510300.SH"),  # 沪市 ETF
            ("510500", "510500.SH"),  # 沪市 ETF
            ("588000", "588000.SH"),  # STAR-tracking ETF (allowlist, not raw 688)
            ("159949", "159949.SZ"),  # 深市 ETF
        ],
    )
    def test_allowed_universe_maps(self, code: str, expected: str) -> None:
        assert _to_tushare_ts_code(code) == expected

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            ("688999", "star_forbidden"),  # STAR raw stock
            ("689999", "star_forbidden"),
            ("430999", "bj_forbidden"),  # 北交
            ("830999", "bj_forbidden"),
            ("920999", "bj_forbidden"),  # NEEQ
            ("110030", "cb_forbidden"),  # SH-listed convertible bond
            ("113008", "cb_forbidden"),
            ("123001", "cb_forbidden"),  # SZ-listed convertible bond
            ("200001", "b_share_forbidden"),  # B-share
            ("900901", "b_share_forbidden"),
        ],
    )
    def test_universe_blocked_prefix_raises_forbidden(
        self, code: str, reason: str
    ) -> None:
        """A universe-blocked code (STAR / BJ / CB / B-share) must fail-closed
        BEFORE reaching the SDK. Old _to_tushare_ts_code's prefix-table guess
        silently routed 688x → .SH and 110x → .SZ (post-review fix)."""
        with pytest.raises(ForbiddenCodeError) as exc:
            _to_tushare_ts_code(code)
        assert exc.value.reason == reason

    @pytest.mark.parametrize("bad", ["999999", "777000", "200001"][:-1])
    # 999999 / 777000 = unknown prefix; 200001 is forbidden (B-share) caught above.
    def test_unknown_prefix_raises_unknown(self, bad: str) -> None:
        with pytest.raises(UnknownCodeError):
            _to_tushare_ts_code(bad)

    @pytest.mark.parametrize("bad", ["", "00021", "0000211", "60000a", "abcdef"])
    def test_malformed_code_raises(self, bad: str) -> None:
        # Shape violations land in UnknownCodeError per stock_metadata.py:160
        with pytest.raises(UnknownCodeError):
            _to_tushare_ts_code(bad)


# ---------------------------------------------------------------------------
# _tushare_sina_row_to_quote
# ---------------------------------------------------------------------------


def _sina_row(**overrides: object) -> pd.Series:
    """Build a sina realtime_quote row with sane defaults.

    Mirrors the actual shape returned by ``ts.realtime_quote(src='sina')`` —
    columns: NAME / TS_CODE / DATE / TIME / OPEN / PRE_CLOSE / PRICE / HIGH /
    LOW / BID / ASK / VOLUME / AMOUNT / A1_P..A5_P / B1_P..B5_P (verified
    against a live call on 2026-05-28). DATE / TIME are no longer used by
    the converter (now uses fetch-time UTC to match the adata primary leg's
    timestamp semantic).
    """
    base = {
        "NAME": "深科技",
        "TS_CODE": "000021.SZ",
        "DATE": "20260528",
        "TIME": "10:13:57",
        "OPEN": 42.11,
        "PRE_CLOSE": 43.98,
        "PRICE": 42.99,
        "HIGH": 43.08,
        "LOW": 41.5,
        "BID": 42.95,
        "ASK": 42.99,
        "VOLUME": 106_125_354,
        "AMOUNT": 4_477_496_326.82,
        "A1_P": 42.99,
        "B1_P": 42.95,
    }
    base.update(overrides)
    return pd.Series(base)


class TestTushareSinaRowToQuote:
    """sina row → :class:`StockQuote` mapping + derivation rules."""

    def test_happy_path_fields_map_correctly(self) -> None:
        quote = _tushare_sina_row_to_quote(_sina_row())
        assert isinstance(quote, StockQuote)
        assert quote.code == "000021"  # 6-digit bare; .SZ stripped
        assert quote.name == "深科技"
        assert quote.price == 42.99
        assert quote.open == 42.11
        assert quote.high == 43.08
        assert quote.low == 41.5
        assert quote.prev_close == 43.98
        assert quote.volume == 106_125_354.0
        assert quote.amount == 4_477_496_326.82

    def test_change_pct_is_derived_from_pre_close(self) -> None:
        """sina row does not carry change_pct directly — derive from
        ``(PRICE - PRE_CLOSE) / PRE_CLOSE * 100`` so the dual-source view
        keeps a consistent ``change_pct`` column for downstream.
        """
        row = _sina_row(PRICE=44.00, PRE_CLOSE=40.00)
        quote = _tushare_sina_row_to_quote(row)
        assert quote.change_pct == pytest.approx(10.0)

    def test_timestamp_is_fetch_time_utc_matching_primary_leg(self) -> None:
        """``timestamp`` mirrors the adata primary leg's ``datetime.now(UTC)``
        fetch-time semantic so per-leg staleness comparisons see a consistent
        epoch. (Old code parsed sina's DATE+TIME exchange clock, which would
        have been systematically older than the wall-clock primary leg —
        review concern #6 from the post-amendment review.)"""
        before = datetime.now(tz=UTC)
        quote = _tushare_sina_row_to_quote(_sina_row())
        after = datetime.now(tz=UTC)
        assert quote.timestamp.tzinfo is not None
        assert quote.timestamp.utcoffset().total_seconds() == 0
        assert before <= quote.timestamp <= after

    def test_turnover_rate_defaults_zero(self) -> None:
        """sina rows do not carry turnover_rate; field is informational on
        the dual-source view (not part of divergence/staleness)."""
        quote = _tushare_sina_row_to_quote(_sina_row())
        assert quote.turnover_rate == 0.0

    def test_missing_pre_close_yields_zero_change_pct(self) -> None:
        """A zero / missing PRE_CLOSE must NOT raise ZeroDivisionError —
        degrade ``change_pct`` to ``0.0`` (informational only)."""
        quote = _tushare_sina_row_to_quote(_sina_row(PRE_CLOSE=0.0))
        assert quote.change_pct == 0.0
        assert quote.prev_close == 0.0

    def test_nan_price_raises_value_error_fail_closed(self) -> None:
        """A NaN PRICE (halted / pre-open / vendor brown-out) MUST raise
        :class:`ValueError` so the dual handler logs ``dual_tushare_sina_failed``
        and sets fallback=None — the P0-8 fail-closed missing-data degrade.
        The old code coerced NaN through ``float(NaN or 0)`` and silently
        shipped a ``StockQuote(price=NaN)`` (code-review finding #1)."""
        row = _sina_row(PRICE=float("nan"))
        with pytest.raises(ValueError, match="no finite positive PRICE"):
            _tushare_sina_row_to_quote(row)

    def test_zero_price_raises_value_error_fail_closed(self) -> None:
        """A zero / non-positive PRICE is equally fail-closed (pre-open
        sentinel or vendor brown-out, never a real quote)."""
        with pytest.raises(ValueError, match="no finite positive PRICE"):
            _tushare_sina_row_to_quote(_sina_row(PRICE=0.0))

    def test_inf_price_raises_value_error_fail_closed(self) -> None:
        """``+inf`` PRICE from a malformed cell must NOT propagate."""
        with pytest.raises(ValueError, match="no finite positive PRICE"):
            _tushare_sina_row_to_quote(_sina_row(PRICE=float("inf")))

    def test_ts_code_without_suffix_yields_full_six_digits(self) -> None:
        """If sina ever returns a bare 6-digit ``TS_CODE`` (defensive),
        the helper keeps it whole."""
        quote = _tushare_sina_row_to_quote(_sina_row(TS_CODE="000021"))
        assert quote.code == "000021"


# ---------------------------------------------------------------------------
# _fetch_stock_tushare_sina (thin SDK wrapper, lazy import)
# ---------------------------------------------------------------------------


class TestFetchStockTushareSina:
    """``_fetch_stock_tushare_sina(code)`` thin wrapper contracts."""

    def test_passes_ts_code_and_src_sina_to_sdk(self) -> None:
        captured: dict[str, str] = {}

        def _fake_realtime_quote(ts_code: str, src: str) -> pd.DataFrame:
            captured["ts_code"] = ts_code
            captured["src"] = src
            return pd.DataFrame([_sina_row().to_dict()])

        # Patch target is ``tushare.realtime_quote`` (the actual SDK symbol)
        # — the file lazy-imports ``tushare`` inside the helper, so a module-
        # level ``backend.data.market_data.ts`` no longer exists.
        with patch("tushare.realtime_quote", side_effect=_fake_realtime_quote):
            df = _fetch_stock_tushare_sina("000021")

        assert captured == {"ts_code": "000021.SZ", "src": "sina"}
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert df.iloc[0]["PRICE"] == 42.99

    def test_propagates_sdk_exception_for_dual_handler(self) -> None:
        """When sina fails (network / parse / SDK error), the helper must
        propagate the exception so ``get_stock_realtime_dual`` can catch it
        and log ``dual_tushare_sina_failed`` — silently swallowing would
        return an empty df and falsely pass the divergence check."""
        with (
            patch("tushare.realtime_quote", side_effect=ConnectionError("sina down")),
            pytest.raises(ConnectionError, match="sina down"),
        ):
            _fetch_stock_tushare_sina("000021")

    def test_universe_blocked_code_raises_before_sdk_call(self) -> None:
        """Universe-blocked prefix (STAR / BJ / CB / B-share) must NOT reach
        the SDK — :func:`classify_board` raises :class:`ForbiddenCodeError`
        with a stable audit reason (post-review fix; old code mapped 688x
        → .SH unconditionally)."""
        with (
            patch("tushare.realtime_quote") as mock_sdk,
            pytest.raises(ForbiddenCodeError),
        ):
            _fetch_stock_tushare_sina("688999")
        mock_sdk.assert_not_called()

    def test_malformed_code_raises_before_sdk_call(self) -> None:
        """Malformed code raises :class:`UnknownCodeError` upfront."""
        with (
            patch("tushare.realtime_quote") as mock_sdk,
            pytest.raises(UnknownCodeError),
        ):
            _fetch_stock_tushare_sina("999999")
        mock_sdk.assert_not_called()
