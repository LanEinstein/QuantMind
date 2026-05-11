"""Reconciliation threshold checker (P0-5 §1.4, B-004).

Pure function: in goes the MockBroker snapshot and the user-reported
mirror, out comes a :class:`DeviationReport`. ``overall_passed=False``
is the only trigger that creates a :class:`ReconciliationTicket`.

Thresholds live in :mod:`backend.models.reconciliation` so the front-end
(P1-5 RiskConfigPanel) reads them without depending on services.
"""

from __future__ import annotations

from backend.models.reconciliation import (
    CASH_TOLERANCE_CNY,
    COST_PRICE_TOLERANCE_CNY,
    DailyReconciliation,
    DeviationReport,
    FieldDeviation,
    MockBrokerSnapshot,
)


def detect_deviations(
    expected: MockBrokerSnapshot,
    actual: DailyReconciliation,
) -> DeviationReport:
    """Compare the MockBroker snapshot with the user-reported mirror.

    The three thresholds (cash 1元, volume 0%, cost 0.01元) are checked
    independently per P0-5 §1.4.1. Any failing field flips the overall
    flag — there is no "tolerance" stacking.
    """
    devs: list[FieldDeviation] = []

    cash_diff = abs(expected.cash - actual.reported_cash)
    devs.append(
        FieldDeviation(
            field="cash",
            expected=f"{expected.cash:.2f}",
            actual=f"{actual.reported_cash:.2f}",
            abs_diff=cash_diff,
            threshold=CASH_TOLERANCE_CNY,
            passed=cash_diff <= CASH_TOLERANCE_CNY,
        )
    )

    expected_by_code = {p.code: p for p in expected.positions}
    actual_by_code = {p.code: p for p in actual.reported_positions}
    all_codes = sorted(set(expected_by_code) | set(actual_by_code))

    for code in all_codes:
        ep = expected_by_code.get(code)
        ap = actual_by_code.get(code)
        if ep is None or ap is None:
            devs.append(
                FieldDeviation(
                    field=f"positions[{code}].presence",
                    expected=("missing" if ep is None else
                              f"vol={ep.volume},cost={ep.cost_price:.2f}"),
                    actual=("missing" if ap is None else
                            f"vol={ap.volume},cost={ap.cost_price:.2f}"),
                    abs_diff=1.0,  # symbolic: presence diff is binary
                    threshold=0.0,
                    passed=False,
                )
            )
            continue

        devs.append(
            FieldDeviation(
                field=f"positions[{code}].volume",
                expected=str(ep.volume),
                actual=str(ap.volume),
                abs_diff=abs(ep.volume - ap.volume),
                threshold=0.0,
                passed=ep.volume == ap.volume,
            )
        )
        cost_diff = abs(ep.cost_price - ap.cost_price)
        devs.append(
            FieldDeviation(
                field=f"positions[{code}].cost_price",
                expected=f"{ep.cost_price:.2f}",
                actual=f"{ap.cost_price:.2f}",
                abs_diff=cost_diff,
                threshold=COST_PRICE_TOLERANCE_CNY,
                passed=cost_diff <= COST_PRICE_TOLERANCE_CNY,
            )
        )

    return DeviationReport(
        ticket_id=actual.ticket_id,
        overall_passed=all(d.passed for d in devs),
        deviations=tuple(devs),
    )


__all__ = ["detect_deviations"]
