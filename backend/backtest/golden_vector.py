"""Lane-2 golden-vector decision oracle (AE-004 §2.4, codex J2).

rqalpha (Lane-1) cross-checks the *execution* — does the order flow reconcile to
the cent? But rqalpha degrades to ``ORACLE_UNAVAILABLE`` whenever its venv is
absent (the common case), leaving the *strategy logic* un-cross-checked. Lane-2
fills that gap: pinned golden vectors of the strategy's per-day decisions
(shortlist / SELL codes / BUY codes / the scores that drove them) are compared
against what the harness produced, under the **fixed-point**
:func:`backend.utils.decision_compare.decision_compare` so a borderline score
cannot flip across numpy versions (NEP 50).

The vectors are authored by hand (test fixtures / pinned expectations), so this
is a regression oracle for the deterministic decision path — independent of the
accounting engine, independent of rqalpha. Codes are compared as ordered tuples
(order is part of the decision: the shortlist rank, the rotation pick); scores
are compared in the fixed-point domain to the chosen precision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from backend.utils.decision_compare import DEFAULT_RATIO_SCALE, decision_compare


@dataclass(frozen=True)
class DecisionVector:
    """One day's strategy decision — golden expectation or produced result.

    ``scores`` is an *assertion subset*: each ``code -> value`` must be
    reproduced by the other side to the fixed-point precision; codes absent
    from a golden vector's ``scores`` are simply not asserted on.
    """

    trade_date: str
    shortlist: tuple[str, ...] = ()
    sell_codes: tuple[str, ...] = ()
    buy_codes: tuple[str, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorDivergence:
    """A single mismatch between a produced and a golden decision vector."""

    trade_date: str
    field_name: str
    expected: str
    produced: str


@dataclass(frozen=True)
class GoldenVectorResult:
    """Outcome of comparing produced decisions to the golden vectors."""

    matched: bool
    divergences: tuple[VectorDivergence, ...]


def _compare_one(
    produced: DecisionVector,
    golden: DecisionVector,
    *,
    score_scale: int,
) -> list[VectorDivergence]:
    out: list[VectorDivergence] = []
    for name in ("shortlist", "sell_codes", "buy_codes"):
        pv = getattr(produced, name)
        gv = getattr(golden, name)
        if pv != gv:
            out.append(
                VectorDivergence(
                    trade_date=golden.trade_date,
                    field_name=name,
                    expected=str(gv),
                    produced=str(pv),
                )
            )
    for code in sorted(golden.scores):
        expected = golden.scores[code]
        if code not in produced.scores:
            out.append(
                VectorDivergence(
                    trade_date=golden.trade_date,
                    field_name=f"score[{code}]",
                    expected=str(expected),
                    produced="<missing>",
                )
            )
            continue
        produced_value = produced.scores[code]
        # Fixed-point equality — a sub-ULP numpy-version difference must not
        # register as a divergence, and a real score change must.
        if not decision_compare(produced_value, expected, "==", scale=score_scale):
            out.append(
                VectorDivergence(
                    trade_date=golden.trade_date,
                    field_name=f"score[{code}]",
                    expected=str(expected),
                    produced=str(produced_value),
                )
            )
    return out


def verify_decision_vectors(
    produced: Sequence[DecisionVector],
    golden: Sequence[DecisionVector],
    *,
    score_scale: int = DEFAULT_RATIO_SCALE,
) -> GoldenVectorResult:
    """Compare produced decisions to the golden vectors (fixed-point scores).

    Returns a structured result (never raises) so every divergence surfaces at
    once. A length / date-alignment mismatch is itself a divergence — a
    shifted decision series must not pass.
    """
    divergences: list[VectorDivergence] = []
    if len(produced) != len(golden):
        divergences.append(
            VectorDivergence(
                trade_date="*",
                field_name="length",
                expected=str(len(golden)),
                produced=str(len(produced)),
            )
        )
        return GoldenVectorResult(matched=False, divergences=tuple(divergences))

    for prod, gold in zip(produced, golden, strict=True):
        if prod.trade_date != gold.trade_date:
            divergences.append(
                VectorDivergence(
                    trade_date=gold.trade_date,
                    field_name="trade_date",
                    expected=gold.trade_date,
                    produced=prod.trade_date,
                )
            )
            continue
        divergences += _compare_one(prod, gold, score_scale=score_scale)
    return GoldenVectorResult(matched=not divergences, divergences=tuple(divergences))


__all__ = [
    "DecisionVector",
    "GoldenVectorResult",
    "VectorDivergence",
    "verify_decision_vectors",
]
