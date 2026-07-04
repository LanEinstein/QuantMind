"""Unit tests for the D2 panel augmenter (pure merge + coverage; no heavy IO).

The real per-day ``daily_basic`` / fundamentals reads are exercised by the full build;
here
we pin the row-preserving left-join, the overwrite/duplicate fail-closed guards, and the
missing-value handling with a duck-typed fake store + fake fundamentals.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from scripts.factor_research.defensive_d2_panel import (
    D2_ADDED_COLUMNS,
    _coverage_by_year,
    augment_panel,
)


class _FakeSnap:
    def __init__(self, payload: bytes) -> None:
        self.raw_payload = payload


class _FakeStore:
    """Returns a ``daily_basic`` snapshot per day from an in-memory ``{day: {code:
    dv}}``."""

    def __init__(self, dv_by_day: dict[str, dict[str, float]]) -> None:
        self._dv = dv_by_day

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN001, ARG002
        rows = self._dv.get(trade_date)
        if rows is None:
            return None
        frame = pd.DataFrame({"ts_code": list(rows), "dv_ratio": list(rows.values())})
        return _FakeSnap(frame.to_csv(index=False).encode("utf-8"))


class _FakeRecord:
    def __init__(self, vals: dict[str, float]) -> None:
        self._vals = vals

    def get(self, field: str) -> float | None:
        return self._vals.get(field)


class _FakeFundamentals:
    def __init__(self, by_key: dict[tuple[str, str], dict[str, float]]) -> None:
        self._by_key = by_key

    def asof(self, code: str, day: str):  # noqa: ANN001
        vals = self._by_key.get((code, day))
        return _FakeRecord(vals) if vals is not None else None


def _crowding_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["20180102", "20180102", "20180109"],
            "code": ["000001", "000002", "000001"],
            "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ"],
            "rev_1d": [0.1, 0.2, 0.3],
        }
    )


def test_augment_preserves_rows_and_adds_three_columns() -> None:
    panel = _crowding_panel()
    store = _FakeStore(
        {
            "20180102": {"000001.SZ": 2.5, "000002.SZ": 1.0},
            "20180109": {"000001.SZ": 3.0},
        }
    )
    fundamentals = _FakeFundamentals(
        {
            ("000001.SZ", "20180102"): {"roe": 12.0, "grossprofit_margin": 30.0},
            ("000002.SZ", "20180102"): {"roe": -5.0, "grossprofit_margin": 8.0},
            # 000001 on 20180109 has no fundamentals record → roe/gpm None.
        }
    )
    out = augment_panel(panel, store, fundamentals)
    assert len(out) == len(panel)  # row-count preserved
    assert list(out.columns) == [*panel.columns, *D2_ADDED_COLUMNS]
    row0 = out[(out["ts_code"] == "000001.SZ") & (out["date"] == "20180102")].iloc[0]
    assert row0["dv_ratio"] == 2.5
    assert row0["roe"] == 12.0
    assert row0["gpm"] == 30.0
    # Missing fundamentals → NaN roe/gpm (fail-closed, never fabricated).
    row2 = out[(out["ts_code"] == "000001.SZ") & (out["date"] == "20180109")].iloc[0]
    assert row2["dv_ratio"] == 3.0
    assert pd.isna(row2["roe"])
    assert pd.isna(row2["gpm"])


def test_augment_refuses_overwrite() -> None:
    panel = _crowding_panel()
    panel["dv_ratio"] = 1.0
    with pytest.raises(ValueError, match="already carries"):
        augment_panel(panel, _FakeStore({}), _FakeFundamentals({}))


def test_missing_daily_basic_day_yields_nan_dividend() -> None:
    panel = _crowding_panel()
    # No snapshot for any day → every dv_ratio NaN (fail-closed).
    out = augment_panel(panel, _FakeStore({}), _FakeFundamentals({}))
    assert out["dv_ratio"].isna().all()


def test_read_csv_parses_only_needed_columns() -> None:
    # A daily_basic snapshot with extra columns still parses (usecols subset).
    frame = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "dv_ratio": [4.0], "pe_ttm": [10.0]}
    )
    parsed = pd.read_csv(
        io.BytesIO(frame.to_csv(index=False).encode("utf-8")),
        usecols=["ts_code", "dv_ratio"],
    )
    assert list(parsed.columns) == ["ts_code", "dv_ratio"]


def test_coverage_by_year() -> None:
    panel = pd.DataFrame(
        {
            "date": ["20150105", "20150105", "20180105"],
            "ts_code": ["a", "b", "c"],
            "dv_ratio": [1.0, None, 2.0],
            "roe": [1.0, None, 3.0],
            "gpm": [1.0, 2.0, None],
        }
    )
    cov = _coverage_by_year(panel)
    assert cov["2015"]["dv_ratio"] == 0.5  # 1 of 2 present
    assert cov["2015"]["quality"] == 0.5  # only 'a' has both roe∧gpm
    assert cov["2018"]["dv_ratio"] == 1.0
    assert cov["2018"]["quality"] == 0.0  # 'c' missing gpm
