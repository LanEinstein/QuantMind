"""Tests for the round-2 panel builder (R2-2 / S5).

Uses a synthetic in-memory snapshot store plus injected fake PIT
fundamentals/industry lookups so the join + neutralization-input columns are
validated deterministically, and the sacred test-window guard still holds.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from scripts.factor_research.build_factor_panel import build_panel_r2
from scripts.factor_research.factor_lib import FACTOR_NAMES, R2_FACTOR_NAMES
from scripts.factor_research.locked_split import LockedSplit

# 320 train_val + 25 embargo + 10 test so the late rebalance dates carry the
# ~253-bar history the 12-1 momentum factor needs.
_DATES = tuple(f"D{i:04d}" for i in range(355))
_TV, _EMB, _TEST = _DATES[:320], _DATES[320:345], _DATES[345:]
_FUND_CODE = "600020.SH"


def _split() -> LockedSplit:
    return LockedSplit(train_val_dates=_TV, embargo_dates=_EMB, test_dates=_TEST)


def _csv(header: str, rows: list[str]) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")


class _FakeStore:
    """Serves per-date snapshots; RAISES if a sealed test date is read."""

    def __init__(self, codes: dict[str, dict[str, list[float]]]) -> None:
        self._codes = codes

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        if trade_date in _TEST:
            raise AssertionError(f"builder read SEALED test date {trade_date}")
        i = _DATES.index(trade_date)
        daily, adj, basic = [], [], []
        for code, d in self._codes.items():
            ts = code + ".SH"
            daily.append(f"{ts},{d['close'][i]},{d['amount'][i]}")
            adj.append(f"{ts},1.0")
            basic.append(f"{ts},{d['turn'][i]},{d['pe'][i]},{d['cmv'][i]}")
        payloads = {
            "daily": _csv("ts_code,close,amount", daily),
            "adj_factor": _csv("ts_code,adj_factor", adj),
            "daily_basic": _csv("ts_code,turnover_rate,pe_ttm,circ_mv", basic),
        }
        return SimpleNamespace(raw_payload=payloads[endpoint])


class _Rec:
    def __init__(self, vals: dict[str, float]) -> None:
        self._vals = vals

    def get(self, field: str) -> float | None:
        return self._vals.get(field)


class _FakeFund:
    """Returns a fundamentals record only for ``_FUND_CODE`` (others → None)."""

    def asof(self, code: str, decision_date: str, *, extra_lag_days: int = 0):  # noqa: ANN201
        if code == _FUND_CODE:
            return _Rec(
                {
                    "roe": 10.0,
                    "grossprofit_margin": 30.0,
                    "netprofit_yoy": 5.0,
                    "or_yoy": 4.0,
                }
            )
        return None


class _FakeIndustry:
    def l1_asof(self, code: str, decision_date: str) -> str | None:
        return "801080.SI" if code.endswith(".SH") else None


def _codes() -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for i in range(1, 26):
        closes = [(10.0 + i) * 1.001**j for j in range(355)]
        out[f"6000{i:02d}"] = {
            "close": closes,
            "amount": [500_000.0] * 355,
            "turn": [2.0] * 355,
            "pe": [15.0] * 355,
            "cmv": [1e5 + i * 1e4] * 355,
        }
    return out


def _build():  # noqa: ANN202
    return build_panel_r2(
        _split(),
        _FakeStore(_codes()),
        fundamentals=_FakeFund(),
        industry=_FakeIndustry(),
        rebalance_freq=64,
    )


def test_r2_panel_columns_and_no_test_reads() -> None:
    panel = _build()
    assert not panel.empty
    assert set(panel["date"]) <= set(_TV)
    assert not (set(panel["date"]) & set(_TEST))
    for col in (
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        "industry_l1",
        "circ_mv",
        "log_circ_mv",
        "fwd_ret_5d",
    ):
        assert col in panel.columns


def test_r2_industry_and_logsize_columns() -> None:
    panel = _build()
    assert (panel["industry_l1"] == "801080.SI").all()
    # log_circ_mv == log(circ_mv) exactly
    diff = (panel["log_circ_mv"] - panel["circ_mv"].map(math.log)).abs()
    assert (diff < 1e-9).all()


def test_r2_fundamentals_join_is_pit_per_code() -> None:
    panel = _build()
    fund_rows = panel[panel["ts_code"] == _FUND_CODE]
    assert not fund_rows.empty
    assert (fund_rows["roe"] == 10.0).all()
    assert (fund_rows["gpm"] == 30.0).all()
    # a different surviving code has no fundamentals → NaN (fail-closed None)
    other = panel[panel["ts_code"] == "600025.SH"]
    assert other["roe"].isna().all()


def test_r2_trend_computed_on_long_history() -> None:
    panel = _build()
    last_date = sorted(panel["date"].unique())[-1]
    late = panel[panel["date"] == last_date]
    # the last rebalance date has ~257 bars ≥ the 253-bar 12-1 momentum need
    assert late["mom_12_1"].notna().any()
    assert late["trend_slope"].notna().any()
