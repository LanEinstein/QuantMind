"""Tests for the future-NaN poison leak gate (main-force-intent P0).

A synthetic in-memory store + two builders — one PIT-clean (features use bars
``<= d``), one deliberately leaky (a feature peeks at ``d+1``) — pin both arms of
the gate: the clean build passes the poison probe, the leaky build is caught.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from scripts.factor_research.leak_probe import (
    MARKET_TRADE_DATE_ENDPOINTS,
    FutureLeakError,
    LeakReport,
    PoisonedStore,
    assert_no_future_leak,
    assert_no_future_leak_sweep,
    check_future_leak,
)

_DATES = ["20200101", "20200102", "20200103", "20200104", "20200105"]
_CODES = ["000001", "000002"]
# A rising close series per code; deterministic, distinct per (code, day).
_CLOSE = {
    ("000001", d): 10.0 + i for i, d in enumerate(_DATES)
}
_CLOSE.update({("000002", d): 20.0 + 2 * i for i, d in enumerate(_DATES)})


class _FakeSnap:
    def __init__(self, raw: bytes) -> None:
        self.raw_payload = raw
        self.trade_date = ""


class _FakeStore:
    """Per-(endpoint, trade_date) CSV-bytes store with a ``latest`` surface."""

    def __init__(self) -> None:
        self._frames: dict[tuple[str, str], bytes] = {}
        for d in _DATES:
            rows = "ts_code,close\n" + "".join(
                f"{c}.SZ,{_CLOSE[(c, d)]}\n" for c in _CODES
            )
            self._frames[("daily", d)] = rows.encode("utf-8")
        # An as-of catalog endpoint (must NOT be poisoned by the market gate).
        self._frames[("index_member_all", "20200105")] = b"ts_code\n000001.SZ\n"

    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> _FakeSnap | None:
        raw = self._frames.get((endpoint, trade_date))
        return _FakeSnap(raw) if raw is not None else None


def _read_close(store: object, day: str) -> dict[str, float]:
    snap = store.latest(vendor="tushare", endpoint="daily", trade_date=day)  # type: ignore[attr-defined]
    if snap is None:
        return {}
    frame = pd.read_csv(io.BytesIO(snap.raw_payload))
    frame["code"] = frame["ts_code"].str.split(".").str[0]
    return dict(zip(frame["code"], frame["close"], strict=True))


def _build_clean(store: object) -> pd.DataFrame:
    """PIT-clean: feature = trailing 1d return (close_d / close_{d-1} - 1)."""
    rows: list[dict[str, object]] = []
    for pos, d in enumerate(_DATES):
        today = _read_close(store, d)
        prev = _read_close(store, _DATES[pos - 1]) if pos >= 1 else {}
        for c in _CODES:
            tc, pc = today.get(c), prev.get(c)
            feat = (tc / pc - 1.0) if (tc and pc) else None
            fwd = None
            if pos + 1 < len(_DATES):
                nxt = _read_close(store, _DATES[pos + 1]).get(c)
                fwd = (nxt / tc - 1.0) if (nxt and tc) else None
            rows.append({"date": d, "code": c, "rev_1d": feat, "fwd_ret_1d": fwd})
    return pd.DataFrame(rows, columns=["date", "code", "rev_1d", "fwd_ret_1d"])


def _build_leaky(store: object) -> pd.DataFrame:
    """Leaky: the FEATURE peeks at close_{d+1} (reads a forward bar)."""
    rows: list[dict[str, object]] = []
    for pos, d in enumerate(_DATES):
        today = _read_close(store, d)
        nxt = _read_close(store, _DATES[pos + 1]) if pos + 1 < len(_DATES) else {}
        for c in _CODES:
            tc, nc = today.get(c), nxt.get(c)
            # The bug: a "feature" that uses tomorrow's close.
            feat = (nc / tc - 1.0) if (tc and nc) else None
            rows.append({"date": d, "code": c, "rev_1d": feat, "fwd_ret_1d": None})
    return pd.DataFrame(rows, columns=["date", "code", "rev_1d", "fwd_ret_1d"])


def test_poisoned_store_hides_future_market_endpoint() -> None:
    store = _FakeStore()
    poisoned = PoisonedStore(store, cutoff="20200103")
    # <= cutoff visible, > cutoff hidden for a poisoned endpoint.
    visible = poisoned.latest(vendor="t", endpoint="daily", trade_date="20200103")
    hidden = poisoned.latest(vendor="t", endpoint="daily", trade_date="20200104")
    assert visible is not None
    assert hidden is None


def test_poisoned_store_passes_through_catalog_endpoint() -> None:
    store = _FakeStore()
    poisoned = PoisonedStore(store, cutoff="20200103")
    # index_member_all is asof-keyed, NOT in the market set → never poisoned even
    # though "20200105" > cutoff (its PIT is guarded by IndustryPIT, not here).
    snap = poisoned.latest(
        vendor="t", endpoint="index_member_all", trade_date="20200105"
    )
    assert snap is not None


def test_clean_build_passes_probe() -> None:
    store = _FakeStore()
    report = assert_no_future_leak(
        _build_clean, store, cutoff="20200104", feature_cols=["rev_1d"]
    )
    assert isinstance(report, LeakReport)
    assert report.leaked is False
    assert report.mismatched_cols == ()
    assert report.vanished_keys == 0
    assert report.n_rows_checked > 0


def test_leaky_build_is_caught() -> None:
    store = _FakeStore()
    report = check_future_leak(
        _build_leaky, store, cutoff="20200104", feature_cols=["rev_1d"]
    )
    assert report.leaked is True
    assert "rev_1d" in report.mismatched_cols
    with pytest.raises(FutureLeakError):
        assert_no_future_leak(
            _build_leaky, store, cutoff="20200104", feature_cols=["rev_1d"]
        )


def test_labels_allowed_to_change_under_poison() -> None:
    # The clean build's fwd_ret_1d DOES change under poison (forward bar vanishes),
    # but it is a LABEL, not in feature_cols → not a leak.
    store = _FakeStore()
    report = check_future_leak(
        _build_clean, store, cutoff="20200104", feature_cols=["rev_1d"]
    )
    assert report.leaked is False


def test_market_endpoint_set_is_frozen_tuple() -> None:
    assert "daily" in MARKET_TRADE_DATE_ENDPOINTS
    assert "index_member_all" not in MARKET_TRADE_DATE_ENDPOINTS
    assert "stk_limit" in MARKET_TRADE_DATE_ENDPOINTS


def test_vacuous_zero_rows_fails_closed() -> None:
    # cutoff before the first date → no rows <= cutoff → must FAIL (review #2).
    store = _FakeStore()
    report = check_future_leak(
        _build_clean, store, cutoff="20191231", feature_cols=["rev_1d"]
    )
    assert report.leaked is True
    assert report.n_rows_checked == 0
    with pytest.raises(FutureLeakError):
        assert_no_future_leak(
            _build_clean, store, cutoff="20191231", feature_cols=["rev_1d"]
        )


def test_missing_feature_col_raises() -> None:
    # A typo / renamed factor must not silently skip its leak check (review #3).
    store = _FakeStore()
    with pytest.raises(ValueError, match="absent from the build"):
        check_future_leak(
            _build_clean, store, cutoff="20200104", feature_cols=["nonexistent_col"]
        )


def test_sweep_passes_clean_builder_over_interior_cutoffs() -> None:
    store = _FakeStore()
    reports = assert_no_future_leak_sweep(
        _build_clean,
        store,
        cutoffs=["20200102", "20200103", "20200104"],
        feature_cols=["rev_1d"],
    )
    assert len(reports) == 3
    assert all(not r.leaked for r in reports)


def test_sweep_catches_interior_leak_a_single_cutoff_would_miss() -> None:
    # The leaky feature at date d reads close_{d+1}. A cutoff at the LAST date would
    # not poison any forward bar for early dates; sweeping interior cutoffs catches
    # the leak at the date whose d+1 crosses the cutoff (review #1).
    store = _FakeStore()
    with pytest.raises(FutureLeakError):
        assert_no_future_leak_sweep(
            _build_leaky,
            store,
            cutoffs=["20200102", "20200103"],
            feature_cols=["rev_1d"],
        )


def test_versions_surface_is_also_poisoned() -> None:
    store = _FakeStore()
    poisoned = PoisonedStore(store, cutoff="20200103")
    # versions() (the other trade_date-keyed surface) is poisoned past the cutoff.
    assert poisoned.versions(vendor="t", endpoint="daily", trade_date="20200104") == []
