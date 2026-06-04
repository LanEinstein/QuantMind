"""Programmatic Alpha158 / Alpha360 factor definitions (Q-002).

WHY generated, not transcribed: both sets are *structured* qlib configs
(MIT) — kbar/price/volume features plus rolling-operator × window grids
(Alpha158) and field × 60-day lag grids (Alpha360). Generating them
reproduces the exact definition strings with zero hand-copy drift and
keeps this file far below the 800-line cap while emitting ~518 factors.

Definitions use qlib's expression DSL (``$close``, ``Ref``, ``Mean`` …)
so a later backtest layer can evaluate them directly (dossier §7.2).
"""

from __future__ import annotations

from typing import NamedTuple


class FactorSeed(NamedTuple):
    """One seed factor — metadata only; evaluation lives in later phases."""

    factor_id: str
    name: str
    category: str  # price / volume / flow / fundamental / sentiment
    definition: str
    source_doc_id: str


_QLIB_DOC = "sourcedoc:qlib-alpha-benchmarks"

# Alpha158 KBAR block — 9 candle-shape features (qlib Alpha158 config).
_KBAR: tuple[tuple[str, str], ...] = (
    ("KMID", "($close-$open)/$open"),
    ("KLEN", "($high-$low)/$open"),
    ("KMID2", "($close-$open)/($high-$low+1e-12)"),
    ("KUP", "($high-Greater($open,$close))/$open"),
    ("KUP2", "($high-Greater($open,$close))/($high-$low+1e-12)"),
    ("KLOW", "(Less($open,$close)-$low)/$open"),
    ("KLOW2", "(Less($open,$close)-$low)/($high-$low+1e-12)"),
    ("KSFT", "(2*$close-$high-$low)/$open"),
    ("KSFT2", "(2*$close-$high-$low)/($high-$low+1e-12)"),
)

# Alpha158 rolling-operator block — operator name -> definition template
# over window {w} (qlib Alpha158 config, windows 5/10/20/30/60).
_ROLLING: tuple[tuple[str, str], ...] = (
    ("ROC", "Ref($close,{w})/$close"),
    ("MA", "Mean($close,{w})/$close"),
    ("STD", "Std($close,{w})/$close"),
    ("BETA", "Slope($close,{w})/$close"),
    ("RSQR", "Rsquare($close,{w})"),
    ("RESI", "Resi($close,{w})/$close"),
    ("MAX", "Max($high,{w})/$close"),
    ("MIN", "Min($low,{w})/$close"),
    ("QTLU", "Quantile($close,{w},0.8)/$close"),
    ("QTLD", "Quantile($close,{w},0.2)/$close"),
    ("RANK", "Rank($close,{w})"),
    ("RSV", "($close-Min($low,{w}))/(Max($high,{w})-Min($low,{w})+1e-12)"),
    ("IMAX", "IdxMax($high,{w})/{w}"),
    ("IMIN", "IdxMin($low,{w})/{w}"),
    ("IMXD", "(IdxMax($high,{w})-IdxMin($low,{w}))/{w}"),
    ("CORR", "Corr($close,Log($volume+1),{w})"),
    ("CORD", "Corr($close/Ref($close,1),Log($volume/Ref($volume,1)+1),{w})"),
    ("CNTP", "Mean($close>Ref($close,1),{w})"),
    ("CNTN", "Mean($close<Ref($close,1),{w})"),
    ("CNTD", "Mean($close>Ref($close,1),{w})-Mean($close<Ref($close,1),{w})"),
    (
        "SUMP",
        "Sum(Greater($close-Ref($close,1),0),{w})"
        "/(Sum(Abs($close-Ref($close,1)),{w})+1e-12)",
    ),
    (
        "SUMN",
        "Sum(Greater(Ref($close,1)-$close,0),{w})"
        "/(Sum(Abs($close-Ref($close,1)),{w})+1e-12)",
    ),
    (
        "SUMD",
        "(Sum(Greater($close-Ref($close,1),0),{w})"
        "-Sum(Greater(Ref($close,1)-$close,0),{w}))"
        "/(Sum(Abs($close-Ref($close,1)),{w})+1e-12)",
    ),
    ("VMA", "Mean($volume,{w})/($volume+1e-12)"),
    ("VSTD", "Std($volume,{w})/($volume+1e-12)"),
    (
        "WVMA",
        "Std(Abs($close/Ref($close,1)-1)*$volume,{w})"
        "/(Mean(Abs($close/Ref($close,1)-1)*$volume,{w})+1e-12)",
    ),
    (
        "VSUMP",
        "Sum(Greater($volume-Ref($volume,1),0),{w})"
        "/(Sum(Abs($volume-Ref($volume,1)),{w})+1e-12)",
    ),
    (
        "VSUMN",
        "Sum(Greater(Ref($volume,1)-$volume,0),{w})"
        "/(Sum(Abs($volume-Ref($volume,1)),{w})+1e-12)",
    ),
    (
        "VSUMD",
        "(Sum(Greater($volume-Ref($volume,1),0),{w})"
        "-Sum(Greater(Ref($volume,1)-$volume,0),{w}))"
        "/(Sum(Abs($volume-Ref($volume,1)),{w})+1e-12)",
    ),
)

_WINDOWS: tuple[int, ...] = (5, 10, 20, 30, 60)

_VOLUME_OPS: frozenset[str] = frozenset(
    {"VMA", "VSTD", "WVMA", "VSUMP", "VSUMN", "VSUMD", "CORR", "CORD"}
)


def alpha158_factors() -> tuple[FactorSeed, ...]:
    """All Alpha158 features: 9 kbar + 4 price + 1 volume + 29 ops x 5 windows."""
    out: list[FactorSeed] = []
    for name, definition in _KBAR:
        out.append(
            FactorSeed(
                factor_id=f"factor:alpha158:{name}",
                name=f"Alpha158 {name}",
                category="price",
                definition=definition,
                source_doc_id=_QLIB_DOC,
            )
        )
    for field in ("OPEN", "HIGH", "LOW", "VWAP"):
        out.append(
            FactorSeed(
                factor_id=f"factor:alpha158:{field}0",
                name=f"Alpha158 {field}0",
                category="price",
                definition=f"${field.lower()}/$close",
                source_doc_id=_QLIB_DOC,
            )
        )
    out.append(
        FactorSeed(
            factor_id="factor:alpha158:VOLUME0",
            name="Alpha158 VOLUME0",
            category="volume",
            definition="$volume/($volume+1e-12)",
            source_doc_id=_QLIB_DOC,
        )
    )
    for op, template in _ROLLING:
        for w in _WINDOWS:
            out.append(
                FactorSeed(
                    factor_id=f"factor:alpha158:{op}{w}",
                    name=f"Alpha158 {op}{w}",
                    category="volume" if op in _VOLUME_OPS else "price",
                    definition=template.format(w=w),
                    source_doc_id=_QLIB_DOC,
                )
            )
    return tuple(out)


def alpha360_factors() -> tuple[FactorSeed, ...]:
    """All 360 Alpha360 features: 6 fields x 60 daily lags, latest-normalised."""
    out: list[FactorSeed] = []
    for field in ("CLOSE", "OPEN", "HIGH", "LOW", "VWAP", "VOLUME"):
        denom = "$volume" if field == "VOLUME" else "$close"
        category = "volume" if field == "VOLUME" else "price"
        for lag in range(60):
            expr = f"${field.lower()}" if lag == 0 else f"Ref(${field.lower()},{lag})"
            out.append(
                FactorSeed(
                    factor_id=f"factor:alpha360:{field}{lag}",
                    name=f"Alpha360 {field}{lag}",
                    category=category,
                    definition=f"{expr}/{denom}",
                    source_doc_id=_QLIB_DOC,
                )
            )
    return tuple(out)


__all__ = ["FactorSeed", "alpha158_factors", "alpha360_factors"]
