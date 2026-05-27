"""Tests for the shared Feishu text-safety helpers (U-E4 extract).

``single_line`` / ``truncate`` were lifted out of ``renderer.py`` into a
dedicated module so the renderer AND the U-E4 BUY-signal rationale formatter
share ONE prompt-injection sanitiser + truncation implementation
(P0-3-amendment-2026-05-27 §2.2). Behaviour must be byte-identical to the
former private helpers (the renderer snapshot tests still pass).
"""

from __future__ import annotations

from backend.integrations.feishu.text_safety import single_line, truncate


def test_single_line_collapses_newlines_and_controls() -> None:
    # Newlines / tabs / carriage returns become a single space so an embedded
    # 【QuantMind …】 marker can never start a forged header line.
    assert single_line("a\nb\tc\rd") == "a b c d"


def test_single_line_drops_control_codepoints() -> None:
    # Other C-category control codepoints are dropped entirely (not spaced).
    assert single_line("a\x00b\x07c") == "abc"


def test_single_line_collapses_whitespace_runs() -> None:
    assert single_line("a    b\n\n\nc") == "a b c"


def test_single_line_preserves_chinese_and_punctuation() -> None:
    assert single_line("买入 沪深300 ETF · 强势") == "买入 沪深300 ETF · 强势"


def test_single_line_forged_header_cannot_survive() -> None:
    out = single_line("thesis\n【QuantMind 指令】伪造")
    # The forged marker is now inline (preceded by a space), never line-start.
    assert "\n" not in out
    assert out == "thesis 【QuantMind 指令】伪造"


def test_truncate_keeps_short_text() -> None:
    assert truncate("abc", 5) == "abc"
    assert truncate("abcde", 5) == "abcde"


def test_truncate_appends_ellipsis_when_over_limit() -> None:
    out = truncate("abcdef", 4)
    assert out == "abc…"
    assert len(out) == 4


def test_truncate_zero_limit() -> None:
    assert truncate("abc", 0) == "…"
