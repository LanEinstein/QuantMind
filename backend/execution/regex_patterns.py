"""Locked execution-report regex patterns (P0-4 §1.2, B-003 SSoT).

Five base report kinds × three prefixes (none / 更正 / 盘后补录) = nine
patterns total. Every pattern is built from a single base string so a
typo in one form would break the test suite immediately.

`PATTERNS_AS_DICT` exposes the raw strings keyed by stable identifiers.
The frontend JS mirror (`frontend/src/utils/executionRegex.ts`) reads
the same identifiers and compares pattern-by-pattern; any drift makes a
vitest assertion fail. This is the only allowed way for the frontend to
parse user reports — P0-4 §1.1.2 forbids LLM-assisted parsing.

All patterns are compiled with ``re.DOTALL`` so a `reason` may contain
newlines (only valid in 未执行 forms — the other forms only allow ASCII
because volume / price / fee have strict shapes).
"""

from __future__ import annotations

import re
from types import MappingProxyType

# Instruction id segment used inside report bodies. BUY/SELL only —
# HOLD never produces a Feishu message (P0-3 §1.3.1).
_IID = r"(?P<instruction_id>QM-\d{8}-\d{6}-\d{6}-(?:BUY|SELL)-\d{3})"
_SIDE = r"(?P<side_zh>买入|卖出)"
_CODE = r"(?P<stock_code>\d{6})"
_NONNEG = r"\d+(?:\.\d+)?"

# P0-4-amendment-2026-05-27 §2.1 — FILLED report is now「成交价 + 股数」
# only (report_schema_version v2). The owner no longer reports 手续费;
# the system derives the fee-inclusive cost. The old v1 form carried a
# trailing ``手续费 <num>`` — its absence here means a pasted v1-format
# report no longer matches and is routed to AMBIGUOUS (fail-closed),
# never silently applied with a phantom fee.
_FILLED_BASE = (
    rf"已执行 {_IID} {_SIDE} {_CODE} (?P<volume>\d+)股 "
    rf"成交价 (?P<fill_price>{_NONNEG})"
)

_PARTIAL_BASE = (
    rf"部分执行 {_IID} {_SIDE} {_CODE} "
    rf"(?P<filled_volume>\d+)股 "
    rf"成交价 (?P<fill_price>{_NONNEG}) "
    rf"剩余未成交 (?P<remain_volume>\d+)股"
)

# DOTALL applies via the compile flag below; reason captures up to 200
# chars (incl. newlines) — the parser enforces the upper bound after
# match because re.fullmatch with `{1,200}` plus DOTALL works correctly.
_UNFILLED_BASE = rf"未执行 {_IID} 原因[::]\s?(?P<reason>.{{1,200}})"


def _compile(body: str) -> re.Pattern[str]:
    return re.compile(rf"^{body}$", flags=re.DOTALL)


# === base forms ===
R_FILLED = _compile(_FILLED_BASE)
R_PARTIAL = _compile(_PARTIAL_BASE)
R_UNFILLED = _compile(_UNFILLED_BASE)

# === amend forms ===
R_AMEND_FILLED = _compile(rf"更正 {_FILLED_BASE}")
R_AMEND_PARTIAL = _compile(rf"更正 {_PARTIAL_BASE}")
R_AMEND_UNFILLED = _compile(rf"更正 {_UNFILLED_BASE}")

# === post-close forms ===
R_POST_CLOSE_FILLED = _compile(rf"盘后补录 {_FILLED_BASE}")
R_POST_CLOSE_PARTIAL = _compile(rf"盘后补录 {_PARTIAL_BASE}")
R_POST_CLOSE_UNFILLED = _compile(rf"盘后补录 {_UNFILLED_BASE}")


PATTERNS_AS_DICT: MappingProxyType[str, str] = MappingProxyType(
    {
        "FILLED": R_FILLED.pattern,
        "PARTIAL": R_PARTIAL.pattern,
        "UNFILLED": R_UNFILLED.pattern,
        "AMEND_FILLED": R_AMEND_FILLED.pattern,
        "AMEND_PARTIAL": R_AMEND_PARTIAL.pattern,
        "AMEND_UNFILLED": R_AMEND_UNFILLED.pattern,
        "POST_CLOSE_FILLED": R_POST_CLOSE_FILLED.pattern,
        "POST_CLOSE_PARTIAL": R_POST_CLOSE_PARTIAL.pattern,
        "POST_CLOSE_UNFILLED": R_POST_CLOSE_UNFILLED.pattern,
    }
)
"""Read-only mapping {pattern_id: pattern_string}; the frontend mirror
imports the same keys to verify equivalence (B-003 acceptance)."""


__all__ = [
    "PATTERNS_AS_DICT",
    "R_AMEND_FILLED",
    "R_AMEND_PARTIAL",
    "R_AMEND_UNFILLED",
    "R_FILLED",
    "R_PARTIAL",
    "R_POST_CLOSE_FILLED",
    "R_POST_CLOSE_PARTIAL",
    "R_POST_CLOSE_UNFILLED",
    "R_UNFILLED",
]
