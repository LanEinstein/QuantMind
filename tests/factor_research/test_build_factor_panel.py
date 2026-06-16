"""Tests for the train_val factor-panel builder.

Uses a synthetic in-memory snapshot store so the panel logic — the sacred
test-window guard, the PIT exclusions, and the forward-return labels — is
validated deterministically and fast (no real data needed).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.factor_research.build_factor_panel import build_panel, build_test_panel
from scripts.factor_research.locked_split import LockedSplit, SacredTestAccessError

# 30 train_val + 10 embargo + 5 test = 45 sequential synthetic trading days.
_DATES = tuple(f"2020{1000 + i:04d}" for i in range(45))  # opaque ordered ids
_TRAIN_VAL = _DATES[:30]
_EMBARGO = _DATES[30:40]
_TEST = _DATES[40:]


def _split() -> LockedSplit:
    return LockedSplit(
        train_val_dates=_TRAIN_VAL, embargo_dates=_EMBARGO, test_dates=_TEST
    )


def _csv(header: str, rows: list[str]) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")


class _FakeStore:
    """Serves per-date CSV snapshots; RAISES if asked for a sealed test date."""

    def __init__(self, codes: dict[str, dict[str, list[float]]]) -> None:
        self._codes = codes

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        if trade_date in _TEST:  # the guard must make this unreachable
            raise AssertionError(f"builder read SEALED test date {trade_date}")
        i = _DATES.index(trade_date)
        daily, adj, basic = [], [], []
        for code, d in self._codes.items():
            ts = code + (".SH" if code.startswith("6") else ".SZ")
            daily.append(f"{ts},{d['close'][i]},{d['amount'][i]}")
            adj.append(f"{ts},1.0")
            basic.append(f"{ts},{d['turn'][i]},{d['pe'][i]},{d['cmv'][i]}")
        payloads = {
            "daily": _csv("ts_code,close,amount", daily),
            "adj_factor": _csv("ts_code,adj_factor", adj),
            "daily_basic": _csv("ts_code,turnover_rate,pe_ttm,circ_mv", basic),
        }
        return SimpleNamespace(raw_payload=payloads[endpoint])


def _code(close0: float, cmv: float, *, drift: float = 0.001) -> dict[str, list[float]]:
    closes = [close0 * (1 + drift) ** i for i in range(45)]
    return {
        "close": closes,
        "amount": [500_000.0] * 45,  # > 2亿 liquidity floor (千元)
        "turn": [2.0] * 45,
        "pe": [15.0] * 45,
        "cmv": [cmv] * 45,
    }


def _codes() -> dict[str, dict[str, list[float]]]:
    # 25 sh_main codes with a spread of market caps; 1 科创 (688) excluded.
    codes = {f"6000{i:02d}": _code(10.0 + i, cmv=1e5 + i * 1e4) for i in range(1, 26)}
    codes["688001"] = _code(20.0, cmv=5e5)  # 科创 -> board-excluded
    return codes


def test_panel_never_reads_test_dates_and_has_expected_shape() -> None:
    split, store = _split(), _FakeStore(_codes())
    # _FakeStore raises if a test date is read; reaching here proves the guard.
    panel = build_panel(split, store, rebalance_freq=5)
    assert not panel.empty
    # only train_val rebalance dates appear; never embargo or test
    assert set(panel["date"]) <= set(_TRAIN_VAL)
    assert not (set(panel["date"]) & set(_TEST))
    for col in ("ret_5d", "ret_20d", "ep_ttm", "fwd_ret_5d", "fwd_ret_10d"):
        assert col in panel.columns


def test_board_and_size_exclusions_applied() -> None:
    panel = build_panel(_split(), _FakeStore(_codes()), rebalance_freq=5)
    # 科创 688 never survives the board whitelist
    assert not (panel["code"] == "688001").any()
    # bottom-30% by circ_mv excluded: the smallest sh_main codes are dropped.
    # 25 codes -> ~7 smallest excluded; the very smallest (600001) must be gone.
    assert not (panel["code"] == "600001").any()
    assert (panel["code"] == "600025").any()  # the largest survives


def test_forward_return_label_is_correct() -> None:
    # Every synthetic code drifts +0.1%/bar, so the 5-bar forward return at
    # every rebalance date must equal (1.001**5 - 1) exactly.
    panel = build_panel(_split(), _FakeStore(_codes()), rebalance_freq=5)
    fwd5 = panel["fwd_ret_5d"].dropna()
    assert len(fwd5) > 0
    assert (abs(fwd5 - (1.001**5 - 1)) < 1e-9).all()


def test_test_guard_raises_if_split_misused() -> None:
    # Directly confirm the guard: asking the split to clear a test date fails.
    with pytest.raises(SacredTestAccessError):
        _split().assert_not_test(_TEST[0])


# --- build_test_panel (Phase 4 one-shot, the sanctioned test reader) ---------
# A bigger synthetic calendar so the TEST_FEATURE_BUFFER_TD=30 buffer fits:
# 40 train_val + 10 embargo + 20 test = 70 sequential synthetic days.
_BIG = tuple(f"2024{2000 + i:04d}" for i in range(70))
_BIG_TV, _BIG_EMB, _BIG_TEST = _BIG[:40], _BIG[40:50], _BIG[50:]


def _big_split() -> LockedSplit:
    return LockedSplit(
        train_val_dates=_BIG_TV, embargo_dates=_BIG_EMB, test_dates=_BIG_TEST
    )


class _FakeStoreAll:
    """Serves per-date snapshots for ANY date — test reads ALLOWED (this is the
    sanctioned Phase-4 reader, unlike the sealing ``_FakeStore`` above)."""

    def __init__(self, codes: dict[str, dict[str, list[float]]]) -> None:
        self._codes = codes

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        i = _BIG.index(trade_date)
        daily, adj, basic = [], [], []
        for code, d in self._codes.items():
            ts = code + (".SH" if code.startswith("6") else ".SZ")
            daily.append(f"{ts},{d['close'][i]},{d['amount'][i]}")
            adj.append(f"{ts},1.0")
            basic.append(f"{ts},{d['turn'][i]},{d['pe'][i]},{d['cmv'][i]}")
        payloads = {
            "daily": _csv("ts_code,close,amount", daily),
            "adj_factor": _csv("ts_code,adj_factor", adj),
            "daily_basic": _csv("ts_code,turnover_rate,pe_ttm,circ_mv", basic),
        }
        return SimpleNamespace(raw_payload=payloads[endpoint])


def _big_codes() -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for i in range(1, 26):
        closes = [(10.0 + i) * 1.001**j for j in range(70)]
        out[f"6000{i:02d}"] = {
            "close": closes,
            "amount": [500_000.0] * 70,
            "turn": [2.0] * 70,
            "pe": [15.0] * 70,
            "cmv": [1e5 + i * 1e4] * 70,
        }
    return out


def test_build_test_panel_rebalances_only_on_test_dates() -> None:
    panel = build_test_panel(
        _big_split(), _FakeStoreAll(_big_codes()), rebalance_freq=5
    )
    assert not panel.empty
    assert set(panel["date"]) <= set(_BIG_TEST)  # never a buffer/non-test date
    assert not (set(panel["date"]) & set(_BIG_TV))


def test_build_test_panel_first_test_date_has_full_feature_window() -> None:
    # The 30-bar non-test buffer must give the FIRST test rebalance date a full
    # 20-day factor window (else early test dates would be silently dropped).
    panel = build_test_panel(
        _big_split(), _FakeStoreAll(_big_codes()), rebalance_freq=5
    )
    first = sorted(panel["date"].unique())[0]
    assert first == _BIG_TEST[0]
    g = panel[panel["date"] == first]
    assert g["ret_20d"].notna().all()
    assert g["vol_20d"].notna().all()


def test_build_test_panel_forward_labels_use_test_bars() -> None:
    # +0.1%/bar drift ⇒ every realised 5-bar forward return (from test bars > d)
    # equals 1.001**5 - 1 exactly.
    panel = build_test_panel(
        _big_split(), _FakeStoreAll(_big_codes()), rebalance_freq=5
    )
    fwd5 = panel["fwd_ret_5d"].dropna()
    assert len(fwd5) > 0
    assert (abs(fwd5 - (1.001**5 - 1)) < 1e-9).all()


def test_build_panel_still_seals_test_after_refactor() -> None:
    # Regression: the shared-helper refactor must keep build_panel fail-closed —
    # _FakeStore raises on any test read, so reaching here proves the guard holds.
    panel = build_panel(_split(), _FakeStore(_codes()), rebalance_freq=5)
    assert not (set(panel["date"]) & set(_TEST))
