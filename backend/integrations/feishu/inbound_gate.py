"""Inbound message authorization gate (P0-2-amendment-2026-05-27).

The ``lark-oapi`` WebSocket receiver hands every accepted message to a
single dispatcher. Before this gate the dispatcher only filtered by
``chat_id`` (the decision group), so *any* member of the decision
group — a teammate pulled into the chat, a stray account — could post a
plain-text line that happens to match the execution-report /
reconciliation regex and have it mirrored onto the MockBroker.

This module adds the **owner open_id allowlist** layer: a message is
``ACCEPT``ed only when it is on the decision chat AND its sender is an
authorized owner. Everything else is dropped fail-closed:

* ``DROP_WRONG_CHAT``  — not the decision chat (behaviour unchanged).
* ``DROP_NOT_OWNER``   — on the decision chat but sender not allowlisted
  → never reaches the parser / applier / broker mirror.

The encrypt/verify-token handshake (SDK ``EventDispatcherHandler``) is
the first defence; this allowlist is the application-layer *authorization*
that sits behind it — the two stack, neither replaces the other.

Red line (CLAUDE.md §2.6 / §2.2): this module is a pure function with
ZERO ``backend.{llm,agents,mirofish}`` imports — the LLM never
participates in inbound authorization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

# env var name carrying the owner open_id allowlist (comma-separated).
OWNER_OPEN_ID_ENV = "FEISHU_OWNER_OPEN_ID"
DECISION_CHAT_ENV = "FEISHU_DECISION_CHAT_ID"


class InboundVerdict(StrEnum):
    """Three-way classification of an inbound decision-chat message."""

    ACCEPT = "accept"
    """Decision chat AND owner sender → route to parser/applier."""
    DROP_WRONG_CHAT = "drop_wrong_chat"
    """Not the decision chat → drop (alert chat / DM / stray group)."""
    DROP_NOT_OWNER = "drop_not_owner"
    """Decision chat but sender not allowlisted → fail-closed drop + audit."""


def _parse_owner_ids(raw: str | Iterable[str]) -> frozenset[str]:
    """Parse the comma-separated owner allowlist into a clean frozenset.

    Accepts either the raw env string (``"ou_a,ou_b"``) or an already-split
    iterable. Empty / whitespace-only entries are dropped so a trailing
    comma or a blank env value never silently widens the allowlist.
    """
    if isinstance(raw, str):
        parts: Iterable[str] = raw.split(",")
    else:
        parts = raw
    return frozenset(p.strip() for p in parts if p and p.strip())


@dataclass(frozen=True)
class InboundGate:
    """Decision-chat + owner-open_id authorization gate (fail-closed)."""

    decision_chat_id: str
    owner_open_ids: frozenset[str]

    def classify(self, *, chat_id: str, sender_id: str) -> InboundVerdict:
        """Classify one inbound message into the single verdict it earns.

        Order matters: wrong-chat is decided first (it covers the alert
        chat / DMs entirely), then sender authorization within the
        decision chat. A blank ``sender_id`` can never match a stripped,
        non-empty allowlist entry, so it falls through to
        ``DROP_NOT_OWNER`` (fail-closed).
        """
        if chat_id != self.decision_chat_id:
            return InboundVerdict.DROP_WRONG_CHAT
        if sender_id not in self.owner_open_ids:
            return InboundVerdict.DROP_NOT_OWNER
        return InboundVerdict.ACCEPT

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> InboundGate:
        """Build from process env, fail-closed on a missing decision chat
        or an empty owner allowlist.

        Raises:
            ValueError: ``FEISHU_DECISION_CHAT_ID`` unset/blank, or the
                parsed ``FEISHU_OWNER_OPEN_ID`` allowlist is empty. An
                empty allowlist would make the gate reject every sender
                (fail-closed), so we refuse to construct rather than ship
                a permanently-deaf inbound path silently.
        """
        decision_chat_id = (env.get(DECISION_CHAT_ENV) or "").strip()
        if not decision_chat_id:
            raise ValueError(
                f"{DECISION_CHAT_ENV} must be the decision group's "
                "open_chat_id (oc_...); empty value forbidden"
            )
        owner_open_ids = _parse_owner_ids(env.get(OWNER_OPEN_ID_ENV) or "")
        if not owner_open_ids:
            raise ValueError(
                f"{OWNER_OPEN_ID_ENV} must list ≥1 owner open_id (ou_..., "
                "comma-separated); an empty allowlist would reject every "
                "inbound report (P0-2-amendment-2026-05-27 fail-closed)"
            )
        return cls(
            decision_chat_id=decision_chat_id,
            owner_open_ids=owner_open_ids,
        )


__all__ = [
    "DECISION_CHAT_ENV",
    "OWNER_OPEN_ID_ENV",
    "InboundGate",
    "InboundVerdict",
]
