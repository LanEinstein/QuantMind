"""U-E4 — Line-1 BUY-signal rationale (判据) formatter.

A **display-only** justification block for the Line-1 BUY Feishu message:
① 量化 — the deterministic screener composite score + each Alpha158 factor +
why the name was selected; ② 推理 — the fund_manager reasoning + the three
mandatory-analyst conclusions from the debate.

This module is a pure formatting **sub-helper of the renderer**: only
:meth:`MessageRenderer.render_buy_signal` invokes :func:`rationale_lines`, and
the renderer remains the single source of truth for the final wire text. The
rationale NEVER enters the ``InstructionPlan`` / ``RiskCheckSummary`` /
idempotency key / parser — it is a render parameter only
(P0-3-amendment-2026-05-27). Every LLM-written free-text field is passed
through the shared :mod:`backend.integrations.feishu.text_safety` sanitiser
(single-line, anti header-spoof) + length truncation before it reaches the
wire.

No LLM / network / ``backend.{llm,agents,mirofish}`` import — pure stdlib + the
text_safety helper.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from backend.integrations.feishu.text_safety import single_line, truncate

# Truncation budgets for the LLM-written free text (chars). The fund_manager
# thesis gets a little more room than each analyst conclusion line.
REASONING_LIMIT: int = 160
CONCLUSION_LIMIT: int = 120

_INSUFFICIENT = "—(数据不足)"
"""Shown when a factor is undefined (None) — never a fabricated value."""

_EMPTY = "—"
"""Shown when an LLM free-text field is empty (a silent agent / no reasoning)."""


@dataclass(frozen=True)
class BuySignalRationale:
    """Display-only justification carried into :meth:`render_buy_signal`.

    A pure data carrier the Line-1 runner builds from the deterministic
    screener :class:`~backend.screening.screener.CandidateRow` (quantitative)
    + the LLM debate ``TeamState`` text (reasoning). It is frozen so a built
    rationale cannot be mutated, and it holds raw inputs only — all
    presentation (labels, %, 亿元) + sanitisation happens in
    :func:`rationale_lines`. It is NEVER stored on an ``InstructionPlan``
    (single construction point M-004) nor consumed by the parser / idempotency
    key (P0-3-amendment-2026-05-27).
    """

    composite_score: float
    factors: tuple[tuple[str, float | None], ...]
    fund_manager_reasoning: str
    fundamental_conclusion: str
    technical_conclusion: str
    risk_officer_conclusion: str


# Per-factor presentation: (operator-readable label, value formatter kind).
# Locked constant table — the screener's FactorVector field names map 1:1 here.
# An unknown name falls back to a sanitised label + generic 4dp formatting so a
# future factor never crashes the render (fail-safe).
_FACTOR_PRESENTATION: Mapping[str, tuple[str, str]] = {
    "momentum_20d": ("动量(20日)", "pct"),
    "ma_ratio_5_20": ("均线比(5/20)", "ratio"),
    "volatility_20d": ("波动率(20日)", "pct"),
    "rsi_14": ("RSI(14)", "num"),
    "avg_amount_20d": ("日均成交额(20日)", "yi"),
}


def _format_factor_value(value: float | None, kind: str) -> str:
    """Format one factor value for display (deterministic, no injection面).

    Fail-closed: a None OR non-finite (NaN/Inf) value renders as 数据不足
    rather than leaking a literal ``nan``/``inf`` onto the operator's wire
    (CLAUDE.md §3 "fail-closed for data corruption"). The screener already
    excludes non-finite scored factors, so this is defensive hardening.
    """
    if value is None or not math.isfinite(value):
        return _INSUFFICIENT
    if kind == "pct":  # a return / stdev of returns → percent, 2dp
        return f"{value * 100:.2f}%"
    if kind == "num":  # RSI 0–100 → 1dp
        return f"{value:.1f}"
    if kind == "yi":  # ¥ traded amount → 亿元, 2dp
        return f"{value / 1e8:.2f} 亿元"
    # "ratio" + any unknown kind → 4dp ratio.
    return f"{value:.4f}"


def _safe_text(text: str, limit: int) -> str:
    """Single-line + truncate an LLM free-text field; empty → ``—``."""
    return truncate(single_line(text), limit) or _EMPTY


def rationale_lines(rationale: BuySignalRationale) -> list[str]:
    """Render the two-part 判据 block as sanitised plain-text lines.

    Returns the lines (no trailing newline) so the renderer can splice them
    into the BUY message between the prominent order block and the 7-section
    dispatch body. Pure function of its argument.
    """
    # 量化判据 — composite score (+ why-selected note) then one compact factor
    # line so the wire stays short while every scored factor is visible.
    factor_parts: list[str] = []
    for name, value in rationale.factors:
        label, kind = _FACTOR_PRESENTATION.get(name, (single_line(name), "ratio"))
        factor_parts.append(f"{label}: {_format_factor_value(value, kind)}")

    score = rationale.composite_score
    score_str = f"{score:.4f}" if math.isfinite(score) else _INSUFFICIENT
    lines = [
        "—— 量化判据 ——",
        f"综合评分: {score_str}(全市场量化筛选 top-N 入选)",
    ]
    if factor_parts:
        lines.append(" · ".join(factor_parts))

    # 推理判据 — fund_manager thesis + the three mandatory-analyst conclusions.
    # Each free-text field is single-lined + truncated before the wire.
    fund_manager = _safe_text(rationale.fund_manager_reasoning, REASONING_LIMIT)
    fundamental = _safe_text(rationale.fundamental_conclusion, CONCLUSION_LIMIT)
    technical = _safe_text(rationale.technical_conclusion, CONCLUSION_LIMIT)
    risk_officer = _safe_text(rationale.risk_officer_conclusion, CONCLUSION_LIMIT)
    lines.extend(
        [
            "—— 推理判据 ——",
            f"基金经理: {fund_manager}",
            f"基本面: {fundamental}",
            f"技术面: {technical}",
            f"风控: {risk_officer}",
        ]
    )
    return lines


__all__ = [
    "CONCLUSION_LIMIT",
    "REASONING_LIMIT",
    "BuySignalRationale",
    "rationale_lines",
]
