"""Return-blind power-precheck invariants (AP05-001).

Closed-form fixtures + monotonicity guards for the DSR back-solve, the disclosed
reference inversion, the go/no-go boundary, and the return-blind / no-backtest
purity of the module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.factor_research import alpha_pivot_power_precheck as pc
from scripts.factor_research.alpha_pivot_spec import POWER_INPUTS


def test_hac_conservative_inflation_closed_form() -> None:
    # H=5, lag=4: 1 + 2*(0.8^2+0.6^2+0.4^2+0.2^2) = 1 + 2*1.20 = 3.40.
    assert pc.hac_conservative_inflation(5) == pytest.approx(3.40)
    assert pc.hac_conservative_inflation(1) == 1.0  # no overlap
    assert pc.hac_conservative_inflation(2) == pytest.approx(1.0 + 2 * (0.5**2))  # 1.5


def test_dsr_from_sr_monotone_increasing() -> None:
    kw = dict(t=497, n_trials=2417, hac=3.4, skew=0.0, kurt=3.0)
    vals = [pc.dsr_from_sr(sr, **kw) for sr in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)]
    assert vals == sorted(vals)
    assert vals[0] < 0.5 < vals[-1]  # brackets the 0.95 root region


def test_solve_sr_req_round_trips_to_target() -> None:
    kw = dict(t=497, n_trials=2417, hac=3.4, skew=0.0, kurt=3.0)
    sr_req = pc.solve_sr_req(target=0.95, **kw)
    assert pc.dsr_from_sr(sr_req, **kw) == pytest.approx(0.95, abs=1e-4)


def test_sr_req_increases_with_n_trials() -> None:
    base = dict(t=497, hac=3.4, target=0.95, skew=0.0, kurt=3.0)
    lo = pc.solve_sr_req(n_trials=100, **base)
    hi = pc.solve_sr_req(n_trials=5000, **base)
    assert hi > lo  # more trials searched → harder bar


def test_sr_req_decreases_with_sample_size() -> None:
    base = dict(n_trials=2417, hac=3.4, target=0.95, skew=0.0, kurt=3.0)
    short = pc.solve_sr_req(t=200, **base)
    long = pc.solve_sr_req(t=2000, **base)
    assert long < short  # more samples → easier bar


def test_sr_ref_inversion_round_trips() -> None:
    # Invert a disclosed DSR, then re-deflate that SR under the same machinery.
    disclosed = 0.005853153181636872  # frontier eq_5 disclosed DSR
    sr_ref = pc.sr_ref_from_disclosed_dsr(
        disclosed, t=496, n_trials=2387, skew=0.0, kurt=3.0
    )
    back = pc.dsr_from_sr(sr_ref, t=496, n_trials=2387, hac=1.0, skew=0.0, kurt=3.0)
    assert back == pytest.approx(disclosed, abs=1e-4)
    assert 0.0 < sr_ref < 0.2  # a weak per-period edge


def test_go_boundary_flips_on_reference_strength(tmp_path: Path) -> None:
    # A synthetic disclosed reference: a strong DSR → larger SR_ref → go;
    # the real weak DSR → no-go. Exercises the K·SR_ref comparison end to end.
    strong = _write_frontier(tmp_path / "strong.json", dsr=0.9999, n=10, t=496)
    weak = _write_frontier(tmp_path / "weak.json", dsr=0.0059, n=2387, t=496)
    assert pc.run_precheck(frontier_result_path=strong)["go"] is True
    assert pc.run_precheck(frontier_result_path=weak)["go"] is False


# Disclosed pure-reversal eq_5 frontier summary scalars (slot-frontier-results
# -2026-06-27.md); hard-coded so the test is hermetic — the real frontier JSON
# lives under gitignored data/ and must not be a test dependency.
_DISCLOSED_EQ5_DSR = 0.005853153181636872
_DISCLOSED_N_TRIALS = 2387
_DISCLOSED_N_PERIODS = 496


def test_run_precheck_real_inputs_is_no_go(tmp_path: Path) -> None:
    # The actual committed power inputs (deflation N=2417, T=497, K=2) against the
    # DISCLOSED eq_5 reference → NO-GO, the pre-expected verdict (binding
    # constraint = alpha quality). Fixture reproduces the disclosed scalars so the
    # test does not depend on the gitignored frontier artifact.
    frontier = _write_frontier(
        tmp_path / "frontier.json",
        dsr=_DISCLOSED_EQ5_DSR,
        n=_DISCLOSED_N_TRIALS,
        t=_DISCLOSED_N_PERIODS,
    )
    res = pc.run_precheck(frontier_result_path=frontier)
    assert res["go"] is False
    assert res["verdict"] == "no-go"
    assert res["sr_req_ann"] > res["k_times_sr_ref_ann"]
    assert res["gap_factor"] > 2.0
    # inputs echo carries the frozen power spec verbatim.
    echo = res["inputs_echo"]
    assert echo["deflation_n"] == POWER_INPUTS.deflation_n == 2417
    assert echo["t_onc_effective"] == POWER_INPUTS.t_onc_effective == 497
    assert res["k_power"] == POWER_INPUTS.k_power == 2.0


def test_load_disclosed_reference_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        pc.load_disclosed_reference(str(tmp_path / "missing.json"))
    empty = tmp_path / "empty.json"
    empty.write_text('{"configs": {}, "n_trials_deflation": 1}', encoding="utf-8")
    with pytest.raises(KeyError):
        pc.load_disclosed_reference(str(empty))


def test_module_is_return_blind() -> None:
    # No backtest / bar-source / panel imports; no forbidden backend subpackages.
    src = Path(pc.__file__).read_text(encoding="utf-8")
    forbidden = (
        "gate_backtest",
        "gate_bar_source",
        "build_qgr_panel",
        "PitBarSource",
        "backend.llm",
        "backend.agents",
        "backend.mirofish",
        "backend.risk",
    )
    for token in forbidden:
        assert token not in src, token


def _write_frontier(path: Path, *, dsr: float, n: int, t: int) -> str:
    import json

    path.write_text(
        json.dumps(
            {"n_trials_deflation": n, "configs": {"eq_5": {"dsr": dsr, "n_periods": t}}}
        ),
        encoding="utf-8",
    )
    return str(path)
