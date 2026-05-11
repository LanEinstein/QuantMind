"""Strict-regex execution-report parsing surface (P0-4 / B-003).

Importable surface:
- :mod:`regex_patterns` — five locked regexes + the export dict that
  the frontend JS mirror reproduces (P1-5 §2 red line 5).
"""

from backend.execution.regex_patterns import (
    PATTERNS_AS_DICT,
    R_AMEND_FILLED,
    R_AMEND_PARTIAL,
    R_AMEND_UNFILLED,
    R_FILLED,
    R_PARTIAL,
    R_POST_CLOSE_FILLED,
    R_POST_CLOSE_PARTIAL,
    R_POST_CLOSE_UNFILLED,
    R_UNFILLED,
)

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
