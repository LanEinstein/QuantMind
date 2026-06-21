"""Tests for the R5 forward-window pipeline (panel builder + ACCRUING status)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.factor_research.build_factor_panel import (
    build_forward_panel_r4,
    forward_trade_dates,
)
from scripts.factor_research.factor_lib import R4_FACTOR_NAMES
from scripts.factor_research.locked_split import LockedSplit
from scripts.factor_research.round4_forward_test import (
    HORIZON,
    MIN_FORWARD_PERIODS,
    ForwardStatus,
    _accruing,
)

# Reuse the round-3/4 synthetic store + fake PIT lookups (date universe _DATES).
from tests.factor_research.test_build_factor_panel_r3 import _DATES, _codes, _FakeStore
from tests.factor_research.test_build_factor_panel_r4 import _r4_inputs


def test_forward_trade_dates_reads_only_post_test_end(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    rows = [
        {"endpoint": "daily", "trade_date": "20260610"},  # <= test_end (excluded)
        {"endpoint": "daily", "trade_date": "20260612"},  # == test_end (excluded)
        {"endpoint": "daily", "trade_date": "20260618"},  # forward
        {"endpoint": "daily", "trade_date": "20260615"},  # forward (out of order)
        {"endpoint": "adj_factor", "trade_date": "20260619"},  # not daily (ignored)
    ]
    (root / "index.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    fwd = forward_trade_dates(str(root), test_end="20260612")
    assert fwd == ["20260615", "20260618"]  # post-test_end, sorted, daily-only


def test_build_forward_panel_r4_rebalances_on_forward_dates_only() -> None:
    # Carve a forward window from the synthetic store's date range: test ends at
    # D0344, the forward window is D0345..D0354 (the store has bars for them via
    # seal_test=False — the sanctioned forward read). The builder must rebalance
    # ONLY on the forward dates, carry the analyst columns, and label D0345 (which
    # has >= HORIZON forward bars after it).
    split = LockedSplit(
        train_val_dates=_DATES[:320],
        embargo_dates=_DATES[320:345],
        test_dates=_DATES[340:345],  # nominal test tail (read as feature buffer)
    )
    forward = list(_DATES[345:355])  # D0345..D0354
    panel = build_forward_panel_r4(
        split,
        _FakeStore(_codes(), seal_test=False),
        inputs=_r4_inputs(),
        forward_dates=forward,
        rebalance_freq=HORIZON,
    )
    assert not panel.empty
    assert set(panel["date"]) <= set(forward)  # rebalances ONLY on forward dates
    for col in (*R4_FACTOR_NAMES, "fwd_ret_5d", "industry_l1"):
        assert col in panel.columns
    # D0345 has D0350 five bars later → a complete forward label exists.
    assert panel.dropna(subset=["fwd_ret_5d"]).shape[0] > 0


def test_accruing_status_when_window_too_short() -> None:
    status = _accruing(["20260615", "20260618"], 0, "too short")
    assert status.status == "ACCRUING"
    assert status.forward_td == 2
    assert status.forward_start == "20260615"
    assert status.forward_end == "20260618"
    assert status.scoreable_periods == 0
    assert status.verdict is None
    assert status.min_periods_for_verdict == MIN_FORWARD_PERIODS


def test_forward_status_is_immutable_and_min_periods_sane() -> None:
    # MIN_FORWARD_PERIODS must exceed the horizon (≥1 full label) and be a real
    # floor (the locked test had 49 periods; even this is a tentative read).
    assert MIN_FORWARD_PERIODS > HORIZON > 0
    assert ForwardStatus.__dataclass_params__.frozen  # type: ignore[attr-defined]
