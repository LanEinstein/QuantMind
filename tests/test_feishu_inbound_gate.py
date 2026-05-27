"""InboundGate tests (P0-2-amendment-2026-05-27 — owner open_id allowlist).

Locks the fail-closed inbound authorization contract: only a message on
the decision chat from an allowlisted owner sender is ACCEPTed; a wrong
chat is DROP_WRONG_CHAT; a decision-chat message from any other sender is
DROP_NOT_OWNER (never reaches the parser/applier/broker mirror). The
``from_env`` factory refuses to construct an allowlist that authorizes
nobody.
"""

from __future__ import annotations

import pytest

from backend.integrations.feishu.inbound_gate import (
    DECISION_CHAT_ENV,
    OWNER_OPEN_ID_ENV,
    InboundGate,
    InboundVerdict,
)

_DECISION = "oc_decision_chat_0001"
_ALERT = "oc_alert_chat_0002"
_OWNER = "ou_owner_open_id_aaa"
_OTHER = "ou_someone_else_bbb"


def _gate(owner_ids: frozenset[str] | None = None) -> InboundGate:
    return InboundGate(
        decision_chat_id=_DECISION,
        owner_open_ids=owner_ids or frozenset({_OWNER}),
    )


# -- classify --------------------------------------------------------------


def test_owner_on_decision_chat_is_accepted() -> None:
    assert (
        _gate().classify(chat_id=_DECISION, sender_id=_OWNER) is InboundVerdict.ACCEPT
    )


def test_wrong_chat_is_dropped_even_for_owner() -> None:
    # The alert chat must never be a mutation surface even if the owner
    # posts there (告警群≠决策群, P0-2-amendment-2026-05-16).
    assert (
        _gate().classify(chat_id=_ALERT, sender_id=_OWNER)
        is InboundVerdict.DROP_WRONG_CHAT
    )


def test_non_owner_on_decision_chat_is_dropped() -> None:
    assert (
        _gate().classify(chat_id=_DECISION, sender_id=_OTHER)
        is InboundVerdict.DROP_NOT_OWNER
    )


def test_blank_sender_is_not_owner() -> None:
    # A stripped, non-empty allowlist entry can never match "" → fail-closed.
    assert (
        _gate().classify(chat_id=_DECISION, sender_id="")
        is InboundVerdict.DROP_NOT_OWNER
    )


def test_wrong_chat_takes_precedence_over_sender() -> None:
    # Wrong chat is decided first; a non-owner on the wrong chat is still
    # reported as wrong-chat (no audit-noise as a sender violation).
    assert (
        _gate().classify(chat_id=_ALERT, sender_id=_OTHER)
        is InboundVerdict.DROP_WRONG_CHAT
    )


def test_multiple_owners_each_accepted() -> None:
    gate = _gate(frozenset({_OWNER, _OTHER}))
    assert gate.classify(chat_id=_DECISION, sender_id=_OWNER) is (InboundVerdict.ACCEPT)
    assert gate.classify(chat_id=_DECISION, sender_id=_OTHER) is (InboundVerdict.ACCEPT)


# -- from_env (fail-closed construction) -----------------------------------


def test_from_env_builds_with_decision_chat_and_owner() -> None:
    gate = InboundGate.from_env(
        {DECISION_CHAT_ENV: _DECISION, OWNER_OPEN_ID_ENV: _OWNER}
    )
    assert gate.decision_chat_id == _DECISION
    assert gate.owner_open_ids == frozenset({_OWNER})


def test_from_env_parses_comma_separated_owners() -> None:
    gate = InboundGate.from_env(
        {
            DECISION_CHAT_ENV: _DECISION,
            # trailing comma + whitespace must not widen the allowlist.
            OWNER_OPEN_ID_ENV: f" {_OWNER} , {_OTHER} ,",
        }
    )
    assert gate.owner_open_ids == frozenset({_OWNER, _OTHER})


def test_from_env_missing_decision_chat_raises() -> None:
    with pytest.raises(ValueError, match=DECISION_CHAT_ENV):
        InboundGate.from_env({OWNER_OPEN_ID_ENV: _OWNER})


def test_from_env_empty_owner_allowlist_raises() -> None:
    with pytest.raises(ValueError, match=OWNER_OPEN_ID_ENV):
        InboundGate.from_env({DECISION_CHAT_ENV: _DECISION, OWNER_OPEN_ID_ENV: ""})


def test_from_env_whitespace_only_owner_allowlist_raises() -> None:
    # " , ," parses to an empty set → authorizes nobody → refuse to build.
    with pytest.raises(ValueError, match=OWNER_OPEN_ID_ENV):
        InboundGate.from_env({DECISION_CHAT_ENV: _DECISION, OWNER_OPEN_ID_ENV: " , ,"})


def test_empty_allowlist_gate_rejects_everyone() -> None:
    # Defence-in-depth: even if a gate were somehow built with an empty
    # allowlist, classify() rejects every sender (never ACCEPT).
    gate = InboundGate(decision_chat_id=_DECISION, owner_open_ids=frozenset())
    assert (
        gate.classify(chat_id=_DECISION, sender_id=_OWNER)
        is InboundVerdict.DROP_NOT_OWNER
    )
