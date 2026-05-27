"""list_feishu_chats tests — pure helpers + structural CLI (zero network).

The read-only ``im/v1/chats`` call itself is owner-run and not exercised
here; these lock the membership verdict + the credential-skip path so the
script never makes a network call in the test suite.
"""

from __future__ import annotations

import pytest

from scripts.list_feishu_chats import (
    evaluate_chat_membership,
    main,
    missing_credentials,
)

_DECISION = "oc_decision_0001"
_ALERT = "oc_alert_0002"
_OTHER = "oc_other_0003"


# -- evaluate_chat_membership ----------------------------------------------


def test_decision_present_and_distinct_is_ok() -> None:
    v = evaluate_chat_membership(
        [_DECISION, _ALERT, _OTHER],
        decision_chat_id=_DECISION,
        alert_chat_id=_ALERT,
    )
    assert v.decision_present is True
    assert v.alert_present is True
    assert v.decision_is_alert is False
    assert v.ok is True


def test_decision_absent_is_not_ok() -> None:
    v = evaluate_chat_membership(
        [_ALERT, _OTHER],
        decision_chat_id=_DECISION,
        alert_chat_id=_ALERT,
    )
    assert v.decision_present is False
    assert v.ok is False


def test_decision_equals_alert_is_not_ok() -> None:
    # Same id configured for both → violates 告警群≠决策群.
    v = evaluate_chat_membership(
        [_DECISION],
        decision_chat_id=_DECISION,
        alert_chat_id=_DECISION,
    )
    assert v.decision_is_alert is True
    assert v.ok is False


def test_no_alert_configured_is_ok_when_decision_present() -> None:
    v = evaluate_chat_membership(
        [_DECISION],
        decision_chat_id=_DECISION,
        alert_chat_id=None,
    )
    assert v.alert_present is False
    assert v.decision_is_alert is False
    assert v.ok is True


# -- missing_credentials + CLI skip path -----------------------------------


def test_missing_credentials_lists_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DECISION_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    missing = missing_credentials()
    assert "FEISHU_APP_ID" in missing
    assert "FEISHU_DECISION_CHAT_ID" in missing


def test_main_skips_without_credentials_zero_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DECISION_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    # No --real flag exists; the skip is driven purely by missing creds, so
    # this can never reach the network in CI.
    rc = main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "missing_credentials" in out
