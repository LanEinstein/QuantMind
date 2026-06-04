"""P0-6-amendment-2026-06-04 — boot-gate vendor reachability probe.

Regression anchor: the 2026-06-04 09:08 pre-open restart was refused by
cond9 because both trading-path legs collapse to ``None`` pre-open (adata
returns an empty frame; the sina row exists but carries ``PRICE == 0`` and
the strict trading-path parser fail-closes on it) — indistinguishable from
a vendor outage under the old ``get_stock_realtime_dual`` reuse. The
probe-specific semantics (row presence only, booleans out) must pass
pre-open while still failing closed on real outages.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.data.vendor_reachability import probe_dual_vendor_reachability

_VALID_CONFIG_YAML = """\
market_data:
  primary: adata
  fallback: akshare
  refresh_interval_seconds: 30
history_data:
  primary: adata
  fallback: baostock
  default_period: 1y
news:
  refresh_interval_seconds: 300
  max_articles_per_fetch: 50
  importance_threshold: 5
"""


def _sina_preopen_frame(code: str = "510300") -> pd.DataFrame:
    """A real-shaped sina row as served pre-open: vendor alive, no trade yet."""
    return pd.DataFrame(
        [
            {
                "TS_CODE": f"{code}.SH",
                "NAME": "300ETF",
                "PRICE": 0.0,  # pre-open: no trade yet — the trading-path
                "PRE_CLOSE": 3.95,  # parser would fail-close on this row.
                "OPEN": 0.0,
                "HIGH": 0.0,
                "LOW": 0.0,
                "VOLUME": 0,
                "AMOUNT": 0,
            }
        ]
    )


def _adata_trading_frame(code: str = "510300") -> pd.DataFrame:
    return pd.DataFrame([{"stock_code": code, "price": 3.97, "change_pct": 0.5}])


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame()


def _raise(exc: Exception):
    def _fetch(code: str) -> pd.DataFrame:
        raise exc

    return _fetch


class TestProbeDualVendorReachability:
    async def test_preopen_adata_empty_sina_zero_price_row_is_reachable(
        self,
    ) -> None:
        # THE 2026-06-04 regression: pre-open must read as "sina leg serving
        # data" (row present, PRICE==0), never as a vendor outage.
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=lambda code: _empty_frame(),
            fallback_fetch=lambda code: _sina_preopen_frame(code),
        )
        assert primary is False
        assert fallback is True

    async def test_both_vendors_serving_rows_is_fully_reachable(self) -> None:
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=lambda code: _adata_trading_frame(code),
            fallback_fetch=lambda code: _sina_preopen_frame(code),
        )
        assert (primary, fallback) == (True, True)

    async def test_both_vendors_raising_is_unreachable(self) -> None:
        # Real outage: both legs blow up → fail-closed (False, False).
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=_raise(ConnectionError("adata down")),
            fallback_fetch=_raise(TimeoutError("sina timeout")),
        )
        assert (primary, fallback) == (False, False)

    async def test_both_vendors_empty_frames_is_unreachable(self) -> None:
        # A vendor that answers with no row for the code proves nothing —
        # stay fail-closed.
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=lambda code: _empty_frame(),
            fallback_fetch=lambda code: _empty_frame(),
        )
        assert (primary, fallback) == (False, False)

    async def test_primary_serving_fallback_raising(self) -> None:
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=lambda code: _adata_trading_frame(code),
            fallback_fetch=_raise(RuntimeError("sina SDK error")),
        )
        assert (primary, fallback) == (True, False)

    async def test_none_frame_counts_as_not_served(self) -> None:
        # Defensive: a fetcher that returns None (SDK quirk) is "not serving".
        primary, fallback = await probe_dual_vendor_reachability(
            "510300",
            primary_fetch=lambda code: None,  # type: ignore[arg-type,return-value]
            fallback_fetch=lambda code: _sina_preopen_frame(code),
        )
        assert (primary, fallback) == (False, True)


class TestMarketDataServiceProbeMethod:
    async def test_service_method_delegates_to_module_fetchers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        # The injected MarketDataService must expose the probe with the
        # adata/tushare-sina legs wired (structural contract consumed by
        # backend/services/pilot_data_probe.py).
        from backend.data import market_data as md_module
        from backend.data.config import load_data_sources_config
        from backend.data.market_data import MarketDataService

        monkeypatch.setattr(
            md_module, "_fetch_stock_adata", lambda code: _empty_frame()
        )
        monkeypatch.setattr(
            md_module,
            "_fetch_stock_tushare_sina",
            lambda code: _sina_preopen_frame(code),
        )
        cfg_path = tmp_path / "ds.yaml"
        cfg_path.write_text(_VALID_CONFIG_YAML, encoding="utf-8")
        svc = MarketDataService(load_data_sources_config(cfg_path))
        assert await svc.probe_quote_vendor_reachability("510300") == (
            False,
            True,
        )
