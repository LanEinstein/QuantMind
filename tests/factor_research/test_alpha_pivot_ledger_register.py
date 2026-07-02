"""Pre-registration invariants for the alpha-pivot trial ledger stamp (AP0-002).

Runs against a TMP ledger (never the real one) to verify the three cut records,
the spec-hash tag, and idempotent re-append. The real-ledger cumulative counts
(2445 / 2417) are checked by the runner's ``main()`` assertion at registration.
"""

from __future__ import annotations

from pathlib import Path

from scripts.factor_research import alpha_pivot_ledger_register as reg
from scripts.factor_research.alpha_pivot_spec import spec_hash
from scripts.factor_research.trial_ledger import TrialLedger


def test_cut_records_shape() -> None:
    recs = reg.cut_records(spec_hash()[:16])
    assert len(recs) == 3
    families = [r.family for r in recs]
    assert families == [
        "qgr.alpha_pivot.ic",
        "qgr.alpha_pivot.attribution",
        "qgr.alpha_pivot.composite",
    ]
    nominal = {r.family: r.n_nominal_trials for r in recs}
    assert nominal == {
        "qgr.alpha_pivot.ic": 28,
        "qgr.alpha_pivot.attribution": 3,
        "qgr.alpha_pivot.composite": 2,
    }
    effective = {r.family: r.effective_count for r in recs}
    assert effective == {
        "qgr.alpha_pivot.ic": 28,
        "qgr.alpha_pivot.attribution": 1,
        "qgr.alpha_pivot.composite": 1,
    }


def test_spec_hash_embedded_and_train_val_window() -> None:
    h16 = spec_hash()[:16]
    for r in reg.cut_records(h16):
        assert f"spec={h16}" in r.description
        assert (r.window_start, r.window_end) == reg.TRAIN_VAL_WINDOW
        assert r.registered_at == reg.REGISTERED_AT


def test_register_adds_30_effective_and_is_idempotent(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "ledger.jsonl")
    base = TrialLedger.with_legacy(ledger_path)
    base_nominal = base.cumulative_nominal_trials()
    base_effective = base.cumulative_effective_trials()

    first = reg.register(ledger_path)
    assert first["records_appended"] == 3
    assert first["records_skipped"] == 0
    assert first["cumulative_nominal"] == base_nominal + 33
    assert first["cumulative_effective"] == base_effective + 30

    # Re-run: content-addressed trial_id → idempotent skip, counts unchanged.
    second = reg.register(ledger_path)
    assert second["records_appended"] == 0
    assert second["records_skipped"] == 3
    assert second["cumulative_nominal"] == first["cumulative_nominal"]
    assert second["cumulative_effective"] == first["cumulative_effective"]


def test_expected_constants_are_base_plus_cut() -> None:
    # 2445 = 2412 + 33 nominal; 2417 = 2387 + 30 effective (pre-AP base).
    assert reg.EXPECTED_NOMINAL - 33 == 2412
    assert reg.EXPECTED_EFFECTIVE - 30 == 2387
