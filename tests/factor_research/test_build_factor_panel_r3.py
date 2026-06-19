"""Tests for the round-3 panel builder (R3-2).

Synthetic in-memory store + injected fake PIT lookups (fundamentals / industry /
statements / namechange) so the join, the three R3 factor columns, the PIT ST
exclusion, and the sacred test-window guard are validated deterministically.
"""

from __future__ import annotations

from types import SimpleNamespace

from scripts.factor_research.build_factor_panel import (
    _R3Inputs,
    build_panel_r3,
    build_test_panel_r3,
)
from scripts.factor_research.factor_lib import (
    FACTOR_NAMES,
    R2_FACTOR_NAMES,
    R3_FACTOR_NAMES,
)
from scripts.factor_research.locked_split import LockedSplit

_DATES = tuple(f"D{i:04d}" for i in range(355))
_TV, _EMB, _TEST = _DATES[:320], _DATES[320:345], _DATES[345:]
# A LARGE-cap code so its absence proves the PIT ST exclusion (not the
# bottom-30% size cut, which would drop a small-cap code anyway).
_FUND_CODE = "600020.SH"
_ST_CODE = "600024.SH"


def _split() -> LockedSplit:
    return LockedSplit(train_val_dates=_TV, embargo_dates=_EMB, test_dates=_TEST)


def _csv(header: str, rows: list[str]) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")


class _FakeStore:
    def __init__(
        self, codes: dict[str, dict[str, list[float]]], *, seal_test: bool = True
    ) -> None:
        self._codes = codes
        self._seal_test = seal_test

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        if self._seal_test and trade_date in _TEST:
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
    def __init__(self, end_date: str, vals: dict[str, float]) -> None:
        self.end_date = end_date
        self._vals = vals

    def get(self, field: str) -> float | None:
        return self._vals.get(field)


class _FakeFund:
    def asof(self, code: str, decision_date: str, *, extra_lag_days: int = 0):  # noqa: ANN201
        if code == _FUND_CODE:
            return _Rec(
                "x",
                {
                    "roe": 10.0,
                    "grossprofit_margin": 30.0,
                    "netprofit_yoy": 5.0,
                    "or_yoy": 4.0,
                },
            )
        return None


class _FakeIndustry:
    def l1_asof(self, code: str, decision_date: str) -> str | None:
        return "801080.SI" if code.endswith(".SH") else None


class _FakeStmt:
    """Returns a fixed {end_date: record} for _FUND_CODE only (others → empty)."""

    def __init__(self, field: str, by_end: dict[str, float]) -> None:
        self._field = field
        self._by_end = by_end

    def as_known(self, code: str, decision_date: str, *, extra_lag_days: int = 0):  # noqa: ANN201
        if code != _FUND_CODE:
            return {}
        return {e: _Rec(e, {self._field: v}) for e, v in self._by_end.items()}


class _FakeSt:
    def __init__(self, st_codes: set[str]) -> None:
        self._st = st_codes

    def is_st_asof(self, code: str, decision_date: str) -> bool:
        return code in self._st


def _profit_ytd() -> dict[str, float]:
    """16 quarters of YTD profit_dedt with a 2021 jump → SUE computable."""
    out: dict[str, float] = {}
    for year, qs in {
        2018: [10.0, 10.0, 10.0, 10.0],
        2019: [10.0, 10.0, 10.0, 10.0],
        2020: [10.0, 10.0, 10.0, 10.0],
        2021: [14.0, 15.0, 16.0, 20.0],
    }.items():
        cum = 0.0
        for i, mmdd in enumerate(("0331", "0630", "0930", "1231")):
            cum += qs[i]
            out[f"{year}{mmdd}"] = cum
    return out


def _inputs() -> _R3Inputs:
    return _R3Inputs(
        fundamentals=_FakeFund(),
        industry=_FakeIndustry(),
        fina_stmt=_FakeStmt("profit_dedt", _profit_ytd()),
        income=_FakeStmt("n_income", {"20201231": 100.0, "20211231": 120.0}),
        cashflow=_FakeStmt("n_cashflow_act", {"20201231": 60.0, "20211231": 50.0}),
        balancesheet=_FakeStmt(
            "total_assets", {"20201231": 1000.0, "20211231": 1200.0}
        ),
        namechange=_FakeSt({_ST_CODE}),
    )


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
    return build_panel_r3(
        _split(), _FakeStore(_codes()), inputs=_inputs(), rebalance_freq=64
    )


def test_r3_panel_has_all_factor_columns_and_no_test_reads() -> None:
    panel = _build()
    assert not panel.empty
    assert not (set(panel["date"]) & set(_TEST))
    for col in (
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        *R3_FACTOR_NAMES,
        "industry_l1",
        "log_circ_mv",
        "fwd_ret_5d",
    ):
        assert col in panel.columns


def test_r3_pit_st_name_excluded() -> None:
    panel = _build()
    present = set(panel["ts_code"])
    # a comparable LARGE-cap non-ST code survives the size cut and is present...
    assert "600023.SH" in present
    # ...but the (equally large-cap) ST code is hard-excluded from the cohort,
    # so its absence is the PIT ST exclusion, not the bottom-30% size cut.
    assert _ST_CODE not in present


def test_r3_statement_factors_computed_for_fund_code() -> None:
    panel = _build()
    fund_rows = panel[panel["ts_code"] == _FUND_CODE]
    assert not fund_rows.empty
    assert fund_rows["sue"].notna().any()
    # accruals on 2021 annual: (120 − 50)/avg(1200,1000) = 70/1100
    assert (fund_rows["accr"].dropna() > 0).all()
    assert fund_rows["asset_growth"].notna().any()
    # a present large-cap code with no statements → all three R3 factors NaN
    other = panel[panel["ts_code"] == "600019.SH"]
    assert not other.empty
    assert other["accr"].isna().all()
    assert other["sue"].isna().all()


def test_build_test_panel_r3_reads_test_and_rebalances_on_test_only() -> None:
    panel = build_test_panel_r3(
        _split(),
        _FakeStore(_codes(), seal_test=False),
        inputs=_inputs(),
        rebalance_freq=5,
    )
    assert not panel.empty
    assert set(panel["date"]) <= set(_TEST)
    assert set(panel["date"]) & set(_TEST)
    for col in (*R3_FACTOR_NAMES, "industry_l1", "fwd_ret_5d"):
        assert col in panel.columns


def test_build_panel_r3_still_seals_test_window() -> None:
    panel = build_panel_r3(
        _split(),
        _FakeStore(_codes(), seal_test=True),
        inputs=_inputs(),
        rebalance_freq=64,
    )
    assert not (set(panel["date"]) & set(_TEST))
