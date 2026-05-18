"""Spotlighting datamarking helpers (X-010 / P2-2 §1.2).

Spotlighting (cf. Microsoft 2024 prompt-injection survey) wraps any
*untrusted* upstream text with explicit sentinel tags so a downstream
prompt assembler can clearly delimit which bytes came from a remote
source. The defence is complementary to the
:class:`backend.evolution.rag_ingester.Sanitiser`:

* The sanitiser **counts** known prompt-injection markers so the
  reviewer sees a high-risk document at a glance.
* Spotlighting **labels** every byte so the LLM prompt sees a clear
  ``Untrusted: ...`` boundary, making it harder for a marker that did
  slip past the counter to escape the boundary.

The tag shape is deliberately fixed (square brackets + colon-separated
metadata) — different from any of the
:data:`backend.evolution.rag_ingester.INJECTION_MARKER_PATTERNS` so
the counter does not double-count the wrapper as injection.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
"""

from __future__ import annotations

import re

BEGIN_TEMPLATE = "[[BEGIN UNTRUSTED:{source}:{external_id}]]"
END_TEMPLATE = "[[END UNTRUSTED:{source}:{external_id}]]"

_SENTINEL_RE = re.compile(
    r"\[\[(?:BEGIN|END) UNTRUSTED:[^\[\]]*\]\]", re.IGNORECASE
)
"""Matches any sentinel-shaped substring (regardless of source +
external_id values). The escape pass neutralises every match so an
attacker-controlled body cannot close the wrapper early (codex review
P2-1)."""


def _escape_sentinels(body: str) -> str:
    """Replace ``[[ ... ]]`` sentinel-shaped substrings with a safe
    visual equivalent so an upstream document cannot inject a closing
    tag that escapes the Spotlighting wrapper."""
    return _SENTINEL_RE.sub(
        lambda match: match.group(0)
        .replace("[[", "⟦⟦")
        .replace("]]", "⟧⟧"),
        body,
    )


def wrap_with_spotlight(
    *, source: str, external_id: str, body: str
) -> str:
    """Surround ``body`` with begin/end sentinel tags.

    ``source`` + ``external_id`` are interpolated verbatim into the
    tag header; callers are expected to validate them upstream (the
    crawler builds them from provider-side identifiers). The body
    has every embedded ``[[BEGIN UNTRUSTED:...]]`` /
    ``[[END UNTRUSTED:...]]`` sequence neutralised before wrap so a
    malicious payload cannot close the wrapper from inside.
    """
    begin = BEGIN_TEMPLATE.format(source=source, external_id=external_id)
    end = END_TEMPLATE.format(source=source, external_id=external_id)
    safe_body = _escape_sentinels(body)
    return f"{begin}\n{safe_body}\n{end}"


def strip_spotlight(text: str) -> str:
    """Remove begin/end tags — useful for assertions in tests.

    The runtime path never strips; only tests / debugging tooling does.
    """
    out_lines = []
    for line in text.splitlines():
        if line.startswith("[[BEGIN UNTRUSTED:") or line.startswith(
            "[[END UNTRUSTED:"
        ):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


__all__ = [
    "BEGIN_TEMPLATE",
    "END_TEMPLATE",
    "_escape_sentinels",
    "strip_spotlight",
    "wrap_with_spotlight",
]
