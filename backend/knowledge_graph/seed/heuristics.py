"""Encoded trader heuristics (Q-002) — the dossier's six named playbooks.

Each entry becomes a ``Heuristic`` node. The ``text`` is OUR OWN concise
encoding of a publicly documented rule (book/method attribution in
``attributed_to``) — not a quotation. Heuristic action text is evidence
material only; it never enters a decision path (P0-10 positive list,
RECOMMENDS_ACTION edge semantics in the dossier §1.4).
"""

from __future__ import annotations

from typing import NamedTuple


class HeuristicSeed(NamedTuple):
    heuristic_id: str
    text: str
    attributed_to: str
    confidence: float  # prior plausibility 0-1, human-tunable later


HEURISTICS: tuple[HeuristicSeed, ...] = (
    # Dual momentum (Antonacci) -------------------------------------------------
    HeuristicSeed(
        "heuristic:dual-momentum:relative",
        "Hold the asset with the highest 12-month relative return within the "
        "candidate set; rotate monthly.",
        "Gary Antonacci — Dual Momentum Investing (2014)",
        0.6,
    ),
    HeuristicSeed(
        "heuristic:dual-momentum:absolute",
        "Only stay long when the chosen asset's 12-month excess return over "
        "the risk-free rate is positive; otherwise rotate to defensive assets.",
        "Gary Antonacci — Dual Momentum Investing (2014)",
        0.6,
    ),
    # Turtle trading (Dennis/Eckhardt) ------------------------------------------
    HeuristicSeed(
        "heuristic:turtle:breakout-entry",
        "Enter long on a breakout above the prior 20-day high; use the 55-day "
        "high for the slower system.",
        "Richard Dennis / William Eckhardt — Turtle rules (1983)",
        0.55,
    ),
    HeuristicSeed(
        "heuristic:turtle:atr-position-sizing",
        "Size positions so that one ATR(20) move equals roughly 1% of account "
        "equity; never add beyond 4 units per market.",
        "Richard Dennis / William Eckhardt — Turtle rules (1983)",
        0.65,
    ),
    HeuristicSeed(
        "heuristic:turtle:exit",
        "Exit longs on a close below the prior 10-day low (20-day for the "
        "slower system) or at a 2-ATR adverse move from entry.",
        "Richard Dennis / William Eckhardt — Turtle rules (1983)",
        0.6,
    ),
    # CAN SLIM (O'Neil) -----------------------------------------------------------
    HeuristicSeed(
        "heuristic:canslim:earnings-acceleration",
        "Prefer stocks with quarterly earnings growth above 25% year-over-year "
        "and accelerating annual earnings.",
        "William O'Neil — How to Make Money in Stocks (CAN SLIM)",
        0.5,
    ),
    HeuristicSeed(
        "heuristic:canslim:new-high-base",
        "Buy as price breaks out of a sound base (cup-with-handle) to a new "
        "high on volume at least 40-50% above average.",
        "William O'Neil — How to Make Money in Stocks (CAN SLIM)",
        0.5,
    ),
    HeuristicSeed(
        "heuristic:canslim:cut-loss-8pct",
        "Cut every loss at 7-8% below purchase price, no exceptions.",
        "William O'Neil — How to Make Money in Stocks (CAN SLIM)",
        0.7,
    ),
    # Minervini SEPA ---------------------------------------------------------------
    HeuristicSeed(
        "heuristic:minervini:trend-template",
        "Only buy stocks in a stage-2 uptrend: price above the 150- and "
        "200-day MAs, the 200-day MA rising for at least 1 month, and price "
        "within 25% of the 52-week high.",
        "Mark Minervini — Trade Like a Stock Market Wizard (SEPA)",
        0.55,
    ),
    HeuristicSeed(
        "heuristic:minervini:vcp",
        "Enter on a volatility contraction pattern: successive pullbacks "
        "shrinking in depth with volume drying up, then a pivot breakout.",
        "Mark Minervini — Trade Like a Stock Market Wizard (SEPA)",
        0.5,
    ),
    HeuristicSeed(
        "heuristic:minervini:risk-first",
        "Define the stop before entry and keep average loss well below "
        "average gain (risk a fraction of expected reward).",
        "Mark Minervini — Trade Like a Stock Market Wizard (SEPA)",
        0.7,
    ),
    # 缠论 (Chanlun) -----------------------------------------------------------------
    HeuristicSeed(
        "heuristic:chanlun:third-buy-point",
        "三买:中枢上移后回调不回到原中枢区间,确认更高级别趋势延续时买入。",
        "缠中说禅 — 教你炒股票(缠论)",
        0.45,
    ),
    HeuristicSeed(
        "heuristic:chanlun:divergence-exit",
        "背驰离场:同级别走势创新高但动能(MACD 面积/幅度)减弱,卖出或减仓。",
        "缠中说禅 — 教你炒股票(缠论)",
        0.45,
    ),
    # Sector rotation -----------------------------------------------------------------
    HeuristicSeed(
        "heuristic:sector-rotation:relative-strength",
        "Overweight the sectors with top-quartile 3-6 month relative strength "
        "versus the market index; review monthly.",
        "Classic sector-rotation playbook (relative strength)",
        0.5,
    ),
    HeuristicSeed(
        "heuristic:sector-rotation:breadth-confirmation",
        "Only treat a sector move as rotation when breadth confirms: a "
        "majority of constituents participate rather than one mega-cap.",
        "Classic sector-rotation playbook (breadth)",
        0.5,
    ),
)

__all__ = ["HEURISTICS", "HeuristicSeed"]
