"""Unit tests for the SLV-1 forward survival runner (pure logic; no store IO)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

from scripts.factor_research import defensive_sleeve_forward as fw
from scripts.factor_research.defensive_sleeve_spec import (
    CONTAINER,
    FORWARD_KILL_SWITCH,
    REBALANCE_FREQ,
    spec_hash,
)

# --------------------------------------------------------------------------- #
# Registration (fail-closed drift guards)                                      #
# --------------------------------------------------------------------------- #


def test_register_writes_frozen_registration(tmp_path: Path) -> None:
    path = tmp_path / "reg.json"
    reg = fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=True
    )
    assert path.exists()
    assert reg.spec_hash == spec_hash()
    assert reg.forward_start == "20260615"
    assert reg.kill_switch == dict(asdict(FORWARD_KILL_SWITCH))
    # Second load (no --register) verifies and returns the same registration.
    again = fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=False
    )
    assert again == reg


def test_missing_registration_without_flag_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--register"):
        fw.load_or_register(
            tmp_path / "absent.json",
            test_end="20260612",
            forward_start="20260615",
            register=False,
        )


def test_spec_hash_drift_aborts(tmp_path: Path) -> None:
    path = tmp_path / "reg.json"
    fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=True
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["spec_hash"] = "0" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="drifted"):
        fw.load_or_register(
            path, test_end="20260612", forward_start="20260615", register=False
        )


def test_kill_switch_drift_aborts(tmp_path: Path) -> None:
    path = tmp_path / "reg.json"
    fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=True
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["kill_switch"]["mdd_kill"] = 0.99  # a silently loosened switch
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="kill-switch"):
        fw.load_or_register(
            path, test_end="20260612", forward_start="20260615", register=False
        )


def test_forward_start_reanchor_aborts(tmp_path: Path) -> None:
    # A backfilled snapshot between test_end and the registered forward_start
    # would silently re-anchor the whole rebalance grid — must abort (codex).
    path = tmp_path / "reg.json"
    fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=True
    )
    with pytest.raises(ValueError, match="re-anchored"):
        fw.load_or_register(
            path, test_end="20260612", forward_start="20260613", register=False
        )


def test_test_end_mismatch_aborts(tmp_path: Path) -> None:
    path = tmp_path / "reg.json"
    fw.load_or_register(
        path, test_end="20260612", forward_start="20260615", register=True
    )
    with pytest.raises(ValueError, match="test_end"):
        fw.load_or_register(
            path, test_end="20991231", forward_start="20260615", register=False
        )


# --------------------------------------------------------------------------- #
# Kill-switch evaluation                                                       #
# --------------------------------------------------------------------------- #


def test_mdd_breach_kills() -> None:
    kill = fw.evaluate_kill_switch(
        sleeve_periods=(0.01,),
        baseline_periods=(0.02,),
        bench_periods=(0.01,),
        realized_mdd=FORWARD_KILL_SWITCH.mdd_kill + 0.01,
    )
    assert "mdd" in kill["breaches"]
    assert fw.forward_status(kill, n_periods=1) == "KILLED"


def test_bear_cum_breach_kills() -> None:
    # A deep sustained benchmark drawdown classifies later periods as bear
    # (the classifier looks back 4 periods); the sleeve losing hard in those
    # bear periods must trip the pre-registered bear_cum kill.
    bench = (-0.10, -0.10, -0.10, -0.10, -0.10, -0.10)
    sleeve = (-0.02, -0.02, -0.02, -0.02, -0.02, -0.02)
    kill = fw.evaluate_kill_switch(
        sleeve_periods=sleeve,
        baseline_periods=sleeve,
        bench_periods=bench,
        realized_mdd=0.10,
    )
    assert kill["bear_period_n"] > 0
    assert kill["bear_cumulative"] < FORWARD_KILL_SWITCH.bear_cum_kill
    assert "bear_cum" in kill["breaches"]


def test_underperf_streak_breach_kills() -> None:
    n = FORWARD_KILL_SWITCH.baseline_underperf_periods
    sleeve = tuple([0.0] * n)
    baseline = tuple([0.01] * n)
    kill = fw.evaluate_kill_switch(
        sleeve_periods=sleeve,
        baseline_periods=baseline,
        bench_periods=tuple([0.01] * n),
        realized_mdd=0.05,
    )
    assert kill["underperf_streak"] == n
    assert "baseline_underperf" in kill["breaches"]


def test_streak_is_trailing_not_total() -> None:
    # Underperf runs broken by one winning period never accumulate.
    sleeve = (0.0, 0.0, 0.0, 0.02, 0.0, 0.0)
    baseline = (0.01, 0.01, 0.01, 0.01, 0.01, 0.01)
    assert fw.trailing_underperf_streak(sleeve, baseline) == 2


def test_no_breach_accruing_then_surviving() -> None:
    kill = fw.evaluate_kill_switch(
        sleeve_periods=(0.01, 0.01),
        baseline_periods=(0.0, 0.0),
        bench_periods=(0.01, 0.01),
        realized_mdd=0.05,
    )
    assert kill["breaches"] == []
    assert fw.forward_status(kill, n_periods=2) == "ACCRUING"
    min_p = FORWARD_KILL_SWITCH.min_forward_periods
    assert fw.forward_status(kill, n_periods=min_p) == "SURVIVING"


def test_empty_window_is_accruing_not_killed() -> None:
    kill = fw.evaluate_kill_switch(
        sleeve_periods=(),
        baseline_periods=(),
        bench_periods=(),
        realized_mdd=float("nan"),
    )
    assert kill["breaches"] == []
    assert fw.forward_status(kill, n_periods=0) == "ACCRUING"


# --------------------------------------------------------------------------- #
# Schedule + advisory                                                          #
# --------------------------------------------------------------------------- #


def test_forward_schedule_monthly_cadence() -> None:
    days = [f"d{i:03d}" for i in range(45)]
    sched = fw.forward_schedule(days)
    assert sched == [days[0], days[REBALANCE_FREQ], days[2 * REBALANCE_FREQ]]


def _synthetic_ranker_table(date: str, n: int = 12) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date] * n,
            "ts_code": [f"{600000 + i}.SH" for i in range(n)],
            "ranker_score": [float(i) for i in range(n)],
            "ranker_pct": [i / (n - 1) for i in range(n)],
            "log_circ_mv": [20.0] * n,
        }
    )


def test_sleeve_advisory_top5_book(monkeypatch: pytest.MonkeyPatch) -> None:
    tbl = _synthetic_ranker_table("20260710")
    monkeypatch.setattr(
        fw, "_roster_names", lambda store, root: {"600011.SH": "华能国际"}
    )
    monkeypatch.setattr(
        fw, "_closes_on", lambda store, day, codes: {c: 10.0 for c in codes}
    )
    adv = fw.sleeve_advisory(
        tbl, asof="20260710", store=object(), snapshot_root="unused"
    )
    holdings = adv["holdings"]
    assert len(holdings) == 5
    # Highest dv_ratio (score) first-class: codes 600011..600007.
    assert holdings[0]["ts_code"] == "600011.SH"
    assert holdings[0]["name"] == "华能国际"
    assert all(h["target_weight_pct"] == CONTAINER.cap_percent for h in holdings)
    assert adv["cash_weight_pct"] == 100.0 - 5 * CONTAINER.cap_percent
    assert "advisory" in adv["note"] or "display-only" in adv["note"]


def test_sleeve_advisory_empty_date_fails_closed() -> None:
    tbl = _synthetic_ranker_table("20260710")
    with pytest.raises(ValueError, match="fail-closed"):
        fw.sleeve_advisory(tbl, asof="20991231", store=object(), snapshot_root="unused")
