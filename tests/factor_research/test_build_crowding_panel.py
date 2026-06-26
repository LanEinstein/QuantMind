"""Tests for the batch-A crowding panel builder + the leak-probe integration.

Synthetic in-memory store (serves daily with RAW high/low + adj/basic/stk_limit;
RAISES on a sealed test date) so the sacred-split guard, the board/size exclusions,
the crowding factors and the forward labels are validated deterministically. The
final test runs the real builder THROUGH the future-NaN poison gate — proving the
PIT-clean crowding panel is empirically leak-free.
"""

from __future__ import annotations

from types import SimpleNamespace

from scripts.factor_research.build_crowding_panel import (
    CROWDING_PANEL_EXTRA_COLS,
    build_crowding_panel,
)
from scripts.factor_research.factor_lib import (
    CROWDING_FACTOR_NAMES,
    FACTOR_NAMES,
    QGR_FACTOR_NAMES,
)
from scripts.factor_research.leak_probe import assert_no_future_leak_sweep
from scripts.factor_research.locked_split import LockedSplit

_DATES = tuple(f"2020{1000 + i:04d}" for i in range(60))
_TRAIN_VAL = _DATES[:45]
_EMBARGO = _DATES[45:55]
_TEST = _DATES[55:]
_MARKET = ("daily", "adj_factor", "daily_basic", "stk_limit")


def _split() -> LockedSplit:
    return LockedSplit(
        train_val_dates=_TRAIN_VAL, embargo_dates=_EMBARGO, test_dates=_TEST
    )


def _csv(header: str, rows: list[str]) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")


class _StubIndustry:
    def l1_asof(self, code: str, decision_date: str) -> str | None:
        return "801080.SI"


class _FakeStore:
    """Serves per-date CSV snapshots with RAW high/low; RAISES on a test date."""

    def __init__(self, codes: dict[str, dict[str, list[float]]]) -> None:
        self._codes = codes

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        if trade_date in _TEST:
            raise AssertionError(f"builder read SEALED test date {trade_date}")
        i = _DATES.index(trade_date)
        daily, adj, basic, limit = [], [], [], []
        for code, d in self._codes.items():
            ts = code + (".SH" if code.startswith("6") else ".SZ")
            pre_close = d["close"][i - 1] if i > 0 else d["close"][i]
            high = d["close"][i] * 1.02 + 0.01 * (i % 3)
            low = d["close"][i] * 0.98 - 0.01 * (i % 2)
            daily.append(
                f"{ts},{high},{low},{d['close'][i]},{pre_close},{d['amount'][i]}"
            )
            adj.append(f"{ts},1.0")
            basic.append(f"{ts},{d['turn'][i]},{d['pe'][i]},{d['cmv'][i]}")
            limit.append(f"{ts},{d['close'][i] * 1.10},{d['close'][i] * 0.90}")
        payloads = {
            "daily": _csv("ts_code,high,low,close,pre_close,amount", daily),
            "adj_factor": _csv("ts_code,adj_factor", adj),
            "daily_basic": _csv("ts_code,turnover_rate,pe_ttm,circ_mv", basic),
            "stk_limit": _csv("ts_code,up_limit,down_limit", limit),
        }
        return (
            SimpleNamespace(raw_payload=payloads[endpoint])
            if endpoint in payloads
            else None
        )


def _code(close0: float, cmv: float, *, drift: float = 0.002) -> dict[str, list[float]]:
    closes = [close0 * (1 + drift) ** i for i in range(60)]
    # A late turnover surge so blowoff is defined + non-degenerate at late dates.
    turn = [2.0] * 40 + [6.0] * 20
    return {
        "close": closes,
        "amount": [500_000.0] * 60,
        "turn": turn,
        "pe": [15.0] * 60,
        "cmv": [cmv] * 60,
    }


def _codes() -> dict[str, dict[str, list[float]]]:
    codes = {f"6000{i:02d}": _code(10.0 + i, cmv=1e5 + i * 1e4) for i in range(1, 26)}
    codes["688001"] = _code(20.0, cmv=5e5)  # 科创 -> board-excluded
    return codes


def _build(store: object):  # noqa: ANN202
    return build_crowding_panel(
        _split(), store, industry=_StubIndustry(), rebalance_freq=5
    )


class TestCrowdingPanel:
    def test_shape_columns_and_no_test_read(self) -> None:
        panel = _build(_FakeStore(_codes()))
        assert not panel.empty
        assert set(panel["date"]) <= set(_TRAIN_VAL)
        assert not (set(panel["date"]) & set(_TEST))
        for col in (
            *FACTOR_NAMES,
            *QGR_FACTOR_NAMES,
            *CROWDING_FACTOR_NAMES,
            "fwd_ret_5d",
            *CROWDING_PANEL_EXTRA_COLS,
        ):
            assert col in panel.columns
        assert (panel["industry_l1"] == "801080.SI").all()
        assert panel["log_circ_mv"].notna().all()

    def test_board_and_size_exclusions(self) -> None:
        panel = _build(_FakeStore(_codes()))
        assert not (panel["code"] == "688001").any()  # 科创 board-excluded
        assert not (panel["code"] == "600001").any()  # bottom-30% size-excluded
        assert (panel["code"] == "600025").any()

    def test_crowding_factors_defined_at_late_rebalance(self) -> None:
        panel = _build(_FakeStore(_codes()))
        late = panel[panel["date"] == panel["date"].max()]
        for col in CROWDING_FACTOR_NAMES:
            assert late[col].notna().any()

    def test_forward_return_label_correct(self) -> None:
        panel = _build(_FakeStore(_codes()))
        fwd5 = panel["fwd_ret_5d"].dropna()
        assert len(fwd5) > 0
        assert (abs(fwd5 - (1.002**5 - 1)) < 1e-9).all()

    def test_builder_is_leak_free_under_poison_sweep(self) -> None:
        # The real builder code path, run through a SWEEP of poison cutoffs (review
        # #1): features for rebalance dates <= each cutoff must be byte-identical
        # when future market bars are poisoned away, at every interior boundary. A
        # leak (a forward bar feeding a feature) at any interior date would fail.
        store = _FakeStore(_codes())
        feature_cols = [*FACTOR_NAMES, *QGR_FACTOR_NAMES, *CROWDING_FACTOR_NAMES]
        reports = assert_no_future_leak_sweep(
            _build,
            store,
            cutoffs=[_TRAIN_VAL[25], _TRAIN_VAL[30], _TRAIN_VAL[35], _TRAIN_VAL[40]],
            feature_cols=feature_cols,
            poisoned_endpoints=_MARKET,
        )
        assert len(reports) == 4
        assert all(not r.leaked for r in reports)
        assert all(r.n_rows_checked > 0 for r in reports)
