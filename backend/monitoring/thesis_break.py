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
    evaluate_condition,
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


# Invalidation templates observable on the 30s intraday tick. SCORE_DECAY needs a
# fresh Line-1 score, computed only on the daily cadence — so it is NOT an intraday
# exemption criterion (the daily path owns it). The exemption requires EVERY one of
# these to be *evaluated* AND intact, so a code is never exempted on a silently
# skipped condition (codex P2 cycle-3).
_INTRADAY_OBSERVABLE_TEMPLATES: frozenset[InvalidationTemplate] = frozenset(
    {InvalidationTemplate.ANCHOR_DRAWDOWN, InvalidationTemplate.TIME_STOP}
)


def intraday_intact_codes(
    theses_by_code: Mapping[str, PositionThesis],
    *,
    price_by_code: Mapping[str, float],
    holding_trade_days_by_code: Mapping[str, int] | None = None,
) -> frozenset[str]:
    """Codes whose thesis is CONFIRMED intact on every intraday-observable condition.

    Used by the long-term-hold take-profit exemption
    (P0-10-amendment-line2-2026-06-03): a code is intact only when it has a fresh
    price AND every intraday-observable invalidation condition (ANCHOR_DRAWDOWN +
    TIME_STOP) is **evaluable** (its input is present) and **not broken**. A
    silently-skipped condition (e.g. holding-days missing → TIME_STOP unevaluable)
    DISQUALIFIES the code: the exemption removes sell pressure, so the fail-safe
    direction is "do not exempt unless confirmed intact" (codex P2 cycle-3) — the
    mirror image of ``evaluate_thesis_breaks``'s "never fabricate a SELL".

    SCORE_DECAY (a fresh Line-1 score, daily cadence only) is deliberately NOT an
    intraday exemption criterion — it is enforced on the daily path. Pure +
    deterministic; zero LLM.
    """
    days = holding_trade_days_by_code or {}
    intact: list[str] = []
    for code, thesis in theses_by_code.items():
        price = price_by_code.get(code)
        if price is None:
            continue  # no fresh quote → cannot confirm intact
        obs = ThesisObservation(
            current_price=price,
            holding_trade_days=days.get(code),
            current_score=None,  # no intraday Line-1 score (daily-only)
        )
        # Track which REQUIRED templates were confirmed intact: a thesis missing
        # ANCHOR_DRAWDOWN or TIME_STOP (the model allows an arbitrary 1–8
        # condition set) must NOT be exempted on the conditions it happens to
        # carry — every required template has to be seen, evaluable and intact
        # (codex P2 cycle-4).
        confirmed_templates: set[InvalidationTemplate] = set()
        disqualified = False
        for cond in thesis.invalidation_conditions:
            if cond.template not in _INTRADAY_OBSERVABLE_TEMPLATES:
                continue  # SCORE_DECAY etc. — daily path owns it
            verdict = evaluate_condition(cond, obs)
            # None = unevaluable (missing input) → cannot confirm intact;
            # True = broken. Either disqualifies the exemption.
            if verdict is None or verdict is True:
                disqualified = True
                break
            confirmed_templates.add(cond.template)
        if not disqualified and _INTRADAY_OBSERVABLE_TEMPLATES <= confirmed_templates:
            intact.append(code)
    return frozenset(intact)


__all__ = [
    "ThesisBreak",
    "evaluate_thesis_breaks",
    "intraday_intact_codes",
]
