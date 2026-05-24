"""Tests for the K-001 Tushare Pro full-market client.

Governing decisions:
* R0 §6 — Tushare via the official Python SDK only (``ts.pro_api`` →
  ``pro.daily/daily_basic/fina_indicator_vip/...``); MCP / agent-skill
  "fetch-at-LLM-inference" modes are forbidden in the runtime data path.
* P0-8-amendment-2026-05-24-tushare-data-source — Tushare added as the
  full-market scan layer; ``TUSHARE_TOKEN`` is a heterogeneous
  credential (os.environ only, never .env, fingerprint-logged).

Every SDK call is mocked — no network, no live token spend. A fake
``pro`` object stands in for ``ts.pro_api(token)``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backend.data.tushare_client import (
    TUSHARE_TOKEN_ENV,
    TushareClient,
    TushareConfigError,
    TushareFetchError,
)

# ---------------------------------------------------------------------------
# Fakes — stand in for the tushare ``pro`` API object (ts.pro_api result)
# ---------------------------------------------------------------------------


class _FakePro:
    """Records the last call per endpoint and returns a canned DataFrame."""

    def __init__(self, frames: dict[str, pd.DataFrame] | None = None) -> None:
        self._frames = frames or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _make(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append((endpoint, kwargs))
        return self._frames.get(
            endpoint, pd.DataFrame({"ts_code": ["000001.SZ"], "endpoint": [endpoint]})
        )

    def daily(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("daily", **kwargs)

    def daily_basic(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("daily_basic", **kwargs)

    def adj_factor(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("adj_factor", **kwargs)

    def fina_indicator_vip(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("fina_indicator_vip", **kwargs)

    def index_daily(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("index_daily", **kwargs)

    def fund_daily(self, **kwargs: Any) -> pd.DataFrame:
        return self._make("fund_daily", **kwargs)


class _RaisingPro(_FakePro):
    """Every endpoint raises — exercises fallback / fail-closed."""

    def _make(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append((endpoint, kwargs))
        raise RuntimeError(f"tushare boom on {endpoint}")


class _RecordingFallback:
    """Duck-typed fallback provider with one async ``fetch`` entrypoint."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch(self, endpoint: str, params: dict[str, Any]) -> pd.DataFrame:
        self.calls.append((endpoint, params))
        return pd.DataFrame({"source": ["fallback"], "endpoint": [endpoint]})


VALID_TOKEN = "a" * 40  # tushare tokens look like a long hex/alnum string


# ---------------------------------------------------------------------------
# Token resolution + fingerprint
# ---------------------------------------------------------------------------


class TestTokenResolution:
    def test_explicit_token_resolved_and_fingerprinted(self) -> None:
        client = TushareClient(pro=_FakePro(), token=VALID_TOKEN)
        # fingerprint is SHA256[:8] — 8 hex chars, never plaintext.
        assert len(client.token_fingerprint) == 8
        assert VALID_TOKEN not in client.token_fingerprint

    def test_token_read_from_environ_when_not_explicit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TUSHARE_TOKEN_ENV, VALID_TOKEN)
        client = TushareClient(pro=_FakePro())
        assert len(client.token_fingerprint) == 8

    def test_missing_token_without_injected_pro_raises_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(TUSHARE_TOKEN_ENV, raising=False)
        with pytest.raises(TushareConfigError):
            TushareClient()

    def test_injected_pro_without_token_is_allowed_for_tests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-built pro (e.g. test fake) does not require a token."""
        monkeypatch.delenv(TUSHARE_TOKEN_ENV, raising=False)
        client = TushareClient(pro=_FakePro())
        assert client.token_fingerprint == ""  # no token to fingerprint


# ---------------------------------------------------------------------------
# Full-market single-pull endpoints
# ---------------------------------------------------------------------------


class TestFullMarketPull:
    @pytest.mark.asyncio
    async def test_daily_full_market_single_call(self) -> None:
        pro = _FakePro({"daily": pd.DataFrame({"ts_code": ["1", "2", "3"]})})
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        df = await client.daily("20260522")
        assert list(df["ts_code"]) == ["1", "2", "3"]
        # exactly one SDK call, full-market by trade_date (no per-stock loop).
        assert pro.calls == [("daily", {"trade_date": "20260522"})]

    @pytest.mark.asyncio
    async def test_daily_basic_full_market(self) -> None:
        pro = _FakePro()
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        await client.daily_basic("20260522")
        assert pro.calls == [("daily_basic", {"trade_date": "20260522"})]

    @pytest.mark.asyncio
    async def test_adj_factor_full_market(self) -> None:
        pro = _FakePro()
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        await client.adj_factor("20260522")
        assert pro.calls == [("adj_factor", {"trade_date": "20260522"})]

    @pytest.mark.asyncio
    async def test_fina_indicator_vip_by_period(self) -> None:
        pro = _FakePro({"fina_indicator_vip": pd.DataFrame({"x": range(7194)})})
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        df = await client.fina_indicator_vip("20251231")
        assert len(df) == 7194  # 5000档 vip confirmed in live test
        assert pro.calls == [("fina_indicator_vip", {"period": "20251231"})]

    @pytest.mark.asyncio
    async def test_index_daily_by_ts_code(self) -> None:
        pro = _FakePro()
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        await client.index_daily(
            "000300.SH", start_date="20260101", end_date="20260522"
        )
        assert pro.calls == [
            (
                "index_daily",
                {
                    "ts_code": "000300.SH",
                    "start_date": "20260101",
                    "end_date": "20260522",
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_fund_daily_full_market(self) -> None:
        pro = _FakePro()
        client = TushareClient(pro=pro, token=VALID_TOKEN)
        await client.fund_daily("20260522")
        assert pro.calls == [("fund_daily", {"trade_date": "20260522"})]


# ---------------------------------------------------------------------------
# Input validation at the boundary
# ---------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["2026-05-22", "2026052", "abcdefgh", ""])
    async def test_bad_trade_date_rejected(self, bad: str) -> None:
        client = TushareClient(pro=_FakePro(), token=VALID_TOKEN)
        with pytest.raises(ValueError):
            await client.daily(bad)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["300", "000300", "000300.XX", "000300.sh"])
    async def test_bad_ts_code_rejected(self, bad: str) -> None:
        client = TushareClient(pro=_FakePro(), token=VALID_TOKEN)
        with pytest.raises(ValueError):
            await client.index_daily(bad)


# ---------------------------------------------------------------------------
# Fallback + fail-closed
# ---------------------------------------------------------------------------


class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_invoked_when_tushare_fails(self) -> None:
        fallback = _RecordingFallback()
        client = TushareClient(
            pro=_RaisingPro(), token=VALID_TOKEN, fallback=fallback
        )
        df = await client.daily("20260522")
        assert list(df["source"]) == ["fallback"]
        assert fallback.calls == [("daily", {"trade_date": "20260522"})]

    @pytest.mark.asyncio
    async def test_no_fallback_raises_fetch_error(self) -> None:
        client = TushareClient(pro=_RaisingPro(), token=VALID_TOKEN)
        with pytest.raises(TushareFetchError):
            await client.daily("20260522")


# ---------------------------------------------------------------------------
# R0 §6 — SDK only, no MCP / skill in the data path
# ---------------------------------------------------------------------------


class TestNoMcpInDataPath:
    def test_module_does_not_import_mcp_or_llm(self) -> None:
        src = Path("backend/data/tushare_client.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        joined = " ".join(imported)
        assert "mcp" not in joined.lower()
        # PIT / LLM-data isolation: the data fetch layer never imports
        # backend.llm / backend.agents / backend.mirofish.
        for forbidden in ("backend.llm", "backend.agents", "backend.mirofish"):
            assert forbidden not in joined

    def test_no_skill_or_mcp_keyword_in_source(self) -> None:
        src = Path("backend/data/tushare_client.py").read_text(encoding="utf-8")
        # No runtime client to an MCP server in the fetch path.
        assert "mcp_client" not in src.lower()
        assert "ModelContextProtocol" not in src
