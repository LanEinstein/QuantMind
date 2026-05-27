"""Shared plain-text safety helpers for outbound Feishu rendering.

Lifted out of :mod:`backend.integrations.feishu.renderer` so the renderer
AND the U-E4 BUY-signal rationale formatter
(:mod:`backend.integrations.feishu.signal_rationale`) share **one**
prompt-injection sanitiser + truncation implementation
(P0-3-amendment-2026-05-27 §2.2). Behaviour is byte-identical to the former
private ``_single_line`` / ``_truncate`` helpers, so the renderer snapshot
tests remain green.

No LLM / network / ``backend.{llm,agents,mirofish}`` import — pure stdlib.
"""

from __future__ import annotations

import unicodedata


def single_line(text: str) -> str:
    """Collapse every newline / control character to a single space.

    Used before interpolating operator- or LLM-controlled free text into a
    template body. Without this normalisation a malicious string could embed
    a literal ``\\n【QuantMind 指令】xxx`` and have it render as if a new
    top-level message header started mid-body (codex review session #14 P2-1
    / CLAUDE.md §2.6).
    """
    out: list[str] = []
    for ch in text:
        if ch in {"\n", "\r", "\t", "\v", "\f"}:
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            # Drop other C-category control codepoints entirely.
            continue
        else:
            out.append(ch)
    # Collapse runs of whitespace to a single space — keeps the body visually
    # clean and prevents wide-spacing tricks.
    return " ".join("".join(out).split())


def truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, appending ``…`` when shortened."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


__all__ = ["single_line", "truncate"]
