"""Pre-register the alpha-pivot cut's trial debt — BEFORE any result artifact.

The fixed prior spec (:mod:`alpha_pivot_spec`) is frozen and hashed; this module
stamps that hash into the non-zeroing trial ledger *up front* so the AP-2 DSR
deflation N accounts for this cut's multiple-testing before a single return is
read (implementation plan §3 / spec outline §4.4). Three append-only records:

  * ``diagnostics`` ``qgr.alpha_pivot.ic``      — 28 nominal / 28 effective
    (analyst 3 + quality 4 factors × 4 horizons IC disclosure; conservative:
    disclosure does not launder debt, so effective = nominal).
  * ``ablation``    ``qgr.alpha_pivot.attribution`` — 3 nominal / 1 effective
    (A1/A2/A3 single-add attribution arms).
  * ``single``      ``qgr.alpha_pivot.composite``   — 2 nominal / 1 effective
    (A4 fixed prior composite × dual containers, joint pass — not best-of).

Cut total: nominal +33, effective +30. On the pre-AP base (nominal 2412 /
effective 2387) this lands at **nominal 2445 / effective 2417** — the A4 DSR
deflation N. ``trial_id`` is content-addressed over the DESIGN, so re-running is
an idempotent skip (the spec hash is embedded in ``description`` because
``TrialRecord`` has no dedicated hash field). ``registered_at`` is an injected
date string (no wall-clock).
"""

from __future__ import annotations

import argparse

from .alpha_pivot_spec import spec_hash
from .trial_ledger import TrialLedger, TrialRecord

DEFAULT_LEDGER_PATH: str = "data/factor_research/mfi_trial_ledger.jsonl"
# train_val window (sealed test never read); pre-declared, frozen.
TRAIN_VAL_WINDOW: tuple[str, str] = ("2015-02-09", "2025-04-25")
REGISTERED_AT: str = "2026-07-02"  # AP-0 session date (fixed; no wall-clock).

# Expected cumulative counts AFTER registration (pre-AP base 2412 / 2387).
EXPECTED_NOMINAL: int = 2445
EXPECTED_EFFECTIVE: int = 2417


def cut_records(spec_hash16: str) -> tuple[TrialRecord, ...]:
    """The three pre-registration records for this cut (design-only, hash-tagged)."""
    win = TRAIN_VAL_WINDOW
    return (
        TrialRecord(
            "AP",
            "diagnostics",
            "qgr.alpha_pivot.ic",
            f"analyst(3)+quality(4) x 4 horizons disclosure-only, spec={spec_hash16}",
            28,
            win[0],
            win[1],
            REGISTERED_AT,
            effective_n=28,
        ),
        TrialRecord(
            "AP",
            "ablation",
            "qgr.alpha_pivot.attribution",
            f"A1/A2/A3 single-add attribution arms, spec={spec_hash16}",
            3,
            win[0],
            win[1],
            REGISTERED_AT,
            effective_n=1,
        ),
        TrialRecord(
            "AP",
            "single",
            "qgr.alpha_pivot.composite",
            f"A4 fixed prior composite x dual containers, spec={spec_hash16}",
            2,
            win[0],
            win[1],
            REGISTERED_AT,
            effective_n=1,
        ),
    )


def register(ledger_path: str) -> dict[str, object]:
    """Idempotently append the cut records; return an audit summary dict."""
    ledger = TrialLedger.with_legacy(ledger_path)
    records = cut_records(spec_hash()[:16])
    appended = [ledger.append(rec) for rec in records]
    nominal = ledger.cumulative_nominal_trials()
    effective = ledger.cumulative_effective_trials()
    return {
        "spec_hash16": spec_hash()[:16],
        "records_appended": sum(appended),
        "records_skipped": len(appended) - sum(appended),
        "cumulative_nominal": nominal,
        "cumulative_effective": effective,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    args = parser.parse_args()
    summary = register(args.ledger)
    print(summary)
    assert summary["cumulative_nominal"] == EXPECTED_NOMINAL, summary
    assert summary["cumulative_effective"] == EXPECTED_EFFECTIVE, summary
    print(
        f"ledger OK: nominal {EXPECTED_NOMINAL} / effective {EXPECTED_EFFECTIVE} "
        f"(spec={summary['spec_hash16']})"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "EXPECTED_EFFECTIVE",
    "EXPECTED_NOMINAL",
    "REGISTERED_AT",
    "TRAIN_VAL_WINDOW",
    "cut_records",
    "register",
]
