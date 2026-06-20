"""Tests for the round-4 panel builder + report_rc firewall (R4-3).

Reuses the round-3 synthetic store / fake PIT lookups (the r4 panel = r3 columns
+ analyst factors) and injects a fake analyst lookup, so the join, the seven
analyst columns, the carried-over r3 columns, and the sacred test-window guard
are validated deterministically. The report_rc month-key firewall is unit-tested
directly (pure, store-free).
"""

from __future__ import annotations

import pytest

from scripts.factor_research.build_factor_panel import (
    _R4Inputs,
    build_panel_r4,
    build_test_panel_r4,
    report_rc_month_keys,
)
from scripts.factor_research.factor_lib import (
    FACTOR_NAMES,
    R2_FACTOR_NAMES,
    R3_FACTOR_NAMES,
    R4_FACTOR_NAMES,
)

# Reuse the round-3 fixture machinery verbatim (same store / split / fakes).
from tests.factor_research.test_build_factor_panel_r3 import (
    _FUND_CODE,
    _TEST,
    _codes,
    _FakeStore,
    _inputs,
    _split,
)


class _FakeAnalyst:
    """Returns a known analyst vector for _FUND_CODE; all-None for everyone else."""

    def factors(
        self,
        code: str,
        decision_date: str,
        *,
        close: float | None,
        staleness_days: int = 90,
        lookback_days: int = 90,
        level_window_days: int = 180,
    ) -> dict[str, float | None]:
        if code == _FUND_CODE and close is not None:
            return {
                "np_rev": 0.05,
                "eps_rev": 0.04,
                "rev_diff": 0.5,
                "rating_chg": 0.33,
                "tp_impl": 0.2,
                "disp": 0.1,
                "cover_chg": 0.0,
            }
        return dict.fromkeys(R4_FACTOR_NAMES, None)


def _r4_inputs() -> _R4Inputs:
    return _R4Inputs(r3=_inputs(), analyst=_FakeAnalyst())


def _build():  # noqa: ANN202
    return build_panel_r4(
        _split(), _FakeStore(_codes()), inputs=_r4_inputs(), rebalance_freq=64
    )


def test_r4_panel_has_all_factor_columns_and_no_test_reads() -> None:
    panel = _build()
    assert not panel.empty
    assert not (set(panel["date"]) & set(_TEST))
    for col in (
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        *R3_FACTOR_NAMES,
        *R4_FACTOR_NAMES,
        "industry_l1",
        "log_circ_mv",
        "fwd_ret_5d",
    ):
        assert col in panel.columns


def test_r4_analyst_factors_joined_for_fund_code() -> None:
    panel = _build()
    fund_rows = panel[panel["ts_code"] == _FUND_CODE]
    assert not fund_rows.empty
    assert (fund_rows["np_rev"].dropna() == 0.05).all()
    assert (fund_rows["disp"].dropna() == 0.1).all()
    # a present large-cap code with no analyst coverage → all R4 columns NaN.
    other = panel[panel["ts_code"] == "600019.SH"]
    assert not other.empty
    for col in R4_FACTOR_NAMES:
        assert other[col].isna().all()


def test_r4_carries_r3_columns() -> None:
    # The r4 panel must still carry the r3 statement factors (the carry set is
    # R3_CARRY ∪ analyst survivors → all carry columns must be neutralisable).
    panel = _build()
    fund_rows = panel[panel["ts_code"] == _FUND_CODE]
    assert fund_rows["accr"].notna().any()
    assert fund_rows["roe"].notna().any()


def test_build_test_panel_r4_reads_test_and_rebalances_on_test_only() -> None:
    panel = build_test_panel_r4(
        _split(),
        _FakeStore(_codes(), seal_test=False),
        inputs=_r4_inputs(),
        rebalance_freq=5,
    )
    assert not panel.empty
    assert set(panel["date"]) <= set(_TEST)
    assert set(panel["date"]) & set(_TEST)
    for col in (*R4_FACTOR_NAMES, *R3_FACTOR_NAMES, "industry_l1", "fwd_ret_5d"):
        assert col in panel.columns


def test_build_panel_r4_still_seals_test_window() -> None:
    panel = build_panel_r4(
        _split(),
        _FakeStore(_codes(), seal_test=True),
        inputs=_r4_inputs(),
        rebalance_freq=64,
    )
    assert not (set(panel["date"]) & set(_TEST))


# --- report_rc firewall (pure, store-free) ----------------------------------


def test_report_rc_firewall_blocks_test_era_months() -> None:
    # train_val_end 20250430 < test_start 20250604 → all keys pre-test (OK).
    keys = report_rc_month_keys(
        "20250430", test_start="20250604", sanctioned_test_read=False
    )
    assert keys[-1] == "20250430"
    assert all(k < "20250604" for k in keys)


def test_report_rc_firewall_raises_on_leak() -> None:
    # A last-date inside/after the sealed window would load test-era months →
    # fail-closed RuntimeError unless explicitly sanctioned.
    with pytest.raises(RuntimeError, match="firewall"):
        report_rc_month_keys(
            "20260612", test_start="20250604", sanctioned_test_read=False
        )


def test_report_rc_firewall_sanctioned_allows_test_era() -> None:
    keys = report_rc_month_keys(
        "20260612", test_start="20250604", sanctioned_test_read=True
    )
    assert keys[-1] == "20260612"
    assert any(k >= "20250604" for k in keys)
