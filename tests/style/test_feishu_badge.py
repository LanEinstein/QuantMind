"""AC-007 — display-only Feishu style badge."""

from __future__ import annotations

from backend.integrations.feishu.renderer import style_badge
from backend.style import StyleTag


def test_short_term_badge() -> None:
    assert style_badge(StyleTag.SHORT_TERM.value) == "⚡短线"


def test_value_badge() -> None:
    assert style_badge(StyleTag.VALUE.value) == "🏛价值"


def test_none_style_is_empty() -> None:
    """A legacy (pre-AC) position renders exactly as before (no badge)."""
    assert style_badge(None) == ""


def test_unknown_style_is_empty() -> None:
    assert style_badge("bogus") == ""


def test_badge_carries_no_order_token() -> None:
    """Display-only: the badge never contains an instruction id or order verb."""
    for style in (StyleTag.SHORT_TERM.value, StyleTag.VALUE.value):
        badge = style_badge(style)
        assert "QM-" not in badge
        assert "BUY" not in badge and "SELL" not in badge
