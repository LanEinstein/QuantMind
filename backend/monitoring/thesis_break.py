"""Deterministic THESIS_QUANT_BREAK evaluation (Phase W-004).

Stage 2 of direction ② (P0-10-amendment-line2-2026-06-01 §1.3): turn a held
position's deterministic ``PositionThesis`` invalidation conditions into SELL
pressure — **zero LLM**, only the pre-approved whitelist quant templates
(ANCHOR_DRAWDOWN / TIME_STOP / SCORE_DECAY). The threshold for each condition was
fixed at buy time (``backend.position_thesis.derivation``); here we only evaluate
the *current* PIT observation against it. The LLM never picks a metric, a
comparator, or a threshold (codex round-1: that would smuggle semantics into the
zero-LLM SELL path).

Pure + deterministic (same thesis + same observation → same break). A condition
whose current value is unavailable (e.g. no fresh Line-1 score intraday) is
**skipped**, never counted as broken — fail-closed: never fabricate a SELL from
missing data.

Module red line (``backend/monitoring/CLAUDE.md`` import isolation, N-005): the
only new import is the **pure** ``backend.position_thesis`` (no
``backend.{llm,agents,agents_team,mirofish}``). The SELL it drives is built
downstream by ``instruction_plan_builder`` (single construction point, R0 §4) —
this module only observes a deterministic break.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from backend.models.position_thesis import (
    InvalidationTemplate,
    PositionThesis,
    ThesisHealth,
)
from backend.position_thesis.evaluation import (
    ThesisObservation,
    evaluate_thesis_health,
)

log = structlog.get_logger(component="monitoring.thesis_break")

_TEMPLATE_LABEL: dict[InvalidationTemplate, str] = {
    InvalidationTemplate.ANCHOR_DRAWDOWN: "跌破买入锚定回撤",
    InvalidationTemplate.TIME_STOP: "超过时间止损窗",
    InvalidationTemplate.SCORE_DECAY: "量化因子分衰减",
}


@dataclass(frozen=True)
class ThesisBreak:
    """A deterministic thesis-invalidation break for one held code."""

    code: str
    broken_templates: tuple[InvalidationTemplate, ...]
    reason: str


def _format_reason(thesis: PositionThesis, broken_labels: tuple[str, ...]) -> str:
    """Human-readable (deterministic) SELL rationale — never LLM."""
    return (
        f"买入逻辑失效(确定性): {thesis.stock_code} 触发 "
        f"{' / '.join(broken_labels)};经 14-check + 飞书人工确认。"
    )


def evaluate_thesis_breaks(
    theses_by_code: Mapping[str, PositionThesis],
    *,
    price_by_code: Mapping[str, float],
    holding_trade_days_by_code: Mapping[str, int] | None = None,
    score_by_code: Mapping[str, float] | None = None,
) -> dict[str, ThesisBreak]:
    """Return ``{code: ThesisBreak}`` for every held thesis that is BROKEN.

    Deterministic over the PIT observation: ANCHOR_DRAWDOWN reads the live price,
    TIME_STOP the holding trading-days, SCORE_DECAY a fresh Line-1 score (skipped
    when absent). A code with no broken condition is omitted (no SELL pressure).
    """
    days = holding_trade_days_by_code or {}
    scores = score_by_code or {}
    out: dict[str, ThesisBreak] = {}
    for code, thesis in theses_by_code.items():
        obs = ThesisObservation(
            current_price=price_by_code.get(code),
            holding_trade_days=days.get(code),
            current_score=scores.get(code),
        )
        result = evaluate_thesis_health(thesis, obs)
        if result.health is not ThesisHealth.BROKEN:
            continue
        templates = tuple(c.template for c in result.broken)
        labels = tuple(_TEMPLATE_LABEL.get(t, t.value) for t in templates)
        out[code] = ThesisBreak(
            code=code,
            broken_templates=templates,
            reason=_format_reason(thesis, labels),
        )
    if out:
        log.info("thesis_breaks_evaluated", codes=sorted(out))
    return out


__all__ = [
    "ThesisBreak",
    "evaluate_thesis_breaks",
]
