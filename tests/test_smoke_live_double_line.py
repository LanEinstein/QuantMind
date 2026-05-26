"""U-D4 live smoke harness — pure verdict logic + zero-network default.

The real-network path (real qwen debate + real Feishu send) is owner-run
and intentionally NOT exercised here. These tests pin the verdict
helpers and prove that the default ``main()`` (no ``--real``) makes zero
network / LLM / Feishu calls.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.integrations.feishu.renderer import MessageRenderer
from scripts.smoke_live_double_line import (
    evaluate_debate_smoke,
    evaluate_feishu_roundtrip,
    main,
    missing_credentials,
)

_DEBATE_AND_FEISHU = (
    "TUSHARE_TOKEN",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFY_TOKEN",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_ALERT_CHAT_ID",
    "FEISHU_DECISION_CHAT_ID",
    "FEISHU_INTERACTIVE_ENABLED",
)


class TestEvaluateDebateSmoke:
    def test_clean_cheap_debate_passes(self) -> None:
        v = evaluate_debate_smoke(
            errors=(),
            spend_before_rmb=0.10,
            spend_after_rmb=0.18,
        )
        assert v.ok is True
        assert v.spend_delta_rmb == pytest.approx(0.08)

    def test_errors_fail(self) -> None:
        v = evaluate_debate_smoke(
            errors=("boom",),
            spend_before_rmb=0.0,
            spend_after_rmb=0.05,
        )
        assert v.ok is False
        assert any("error" in r for r in v.reasons)

    def test_usage_not_counted_fails(self) -> None:
        # Spend did not move → the llm:usage counter was not written.
        v = evaluate_debate_smoke(
            errors=(), spend_before_rmb=0.5, spend_after_rmb=0.5
        )
        assert v.ok is False
        assert any("not counted" in r for r in v.reasons)

    def test_runaway_spend_fails(self) -> None:
        v = evaluate_debate_smoke(
            errors=(), spend_before_rmb=0.0, spend_after_rmb=2.0
        )
        assert v.ok is False
        assert any("smoke budget" in r for r in v.reasons)

    def test_at_hard_cap_fails(self) -> None:
        v = evaluate_debate_smoke(
            errors=(), spend_before_rmb=19.5, spend_after_rmb=20.0
        )
        assert v.ok is False
        assert any("hard cap" in r for r in v.reasons)

    def test_missing_spend_reading_fails_closed(self) -> None:
        v = evaluate_debate_smoke(
            errors=(), spend_before_rmb=None, spend_after_rmb=None
        )
        assert v.ok is False
        assert any("unreadable" in r for r in v.reasons)


class TestEvaluateFeishuRoundtrip:
    def test_accepted_with_message_id_passes(self) -> None:
        v = evaluate_feishu_roundtrip(send_ok=True, message_id="om_abc")
        assert v.ok is True

    def test_rejected_fails(self) -> None:
        v = evaluate_feishu_roundtrip(send_ok=False, message_id=None)
        assert v.ok is False

    def test_accepted_without_message_id_fails(self) -> None:
        v = evaluate_feishu_roundtrip(send_ok=True, message_id=None)
        assert v.ok is False


class TestMissingCredentials:
    def test_reports_all_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in _DEBATE_AND_FEISHU:
            monkeypatch.delenv(name, raising=False)
        missing = missing_credentials(include_feishu=True)
        assert "DASHSCOPE_API_KEY" in missing
        assert "FEISHU_DECISION_CHAT_ID" in missing
        # from_env() requires the full 5-credential pool, incl. the alert
        # chat id — the preflight must declare it (Codex U-D4 verify P2).
        assert "FEISHU_ALERT_CHAT_ID" in missing
        assert "FEISHU_INTERACTIVE_ENABLED=true" in missing

    def test_no_feishu_still_needs_full_debate_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --no-feishu still builds the real frame + initialises the
        # router, so the whole debate credential pool is required.
        for name in _DEBATE_AND_FEISHU:
            monkeypatch.delenv(name, raising=False)
        missing = missing_credentials(include_feishu=False)
        assert set(missing) == {
            "TUSHARE_TOKEN",
            "DASHSCOPE_API_KEY",
            "DEEPSEEK_API_KEY",
            "MOONSHOT_API_KEY",
        }
        # Feishu creds are NOT required when --no-feishu.
        assert "FEISHU_DECISION_CHAT_ID" not in missing

    def test_interactive_disabled_is_a_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in _DEBATE_AND_FEISHU:
            monkeypatch.setenv(name, "x")
        # Present but disabled → sending stays gated.
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "false")
        missing = missing_credentials(include_feishu=True)
        assert missing == ("FEISHU_INTERACTIVE_ENABLED=true",)

    def test_all_present_and_enabled_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in _DEBATE_AND_FEISHU:
            monkeypatch.setenv(name, "x")
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "true")
        assert missing_credentials(include_feishu=True) == ()


class TestMainDefaultMakesNoNetworkCalls:
    def test_default_run_returns_zero_without_real(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # If main() tried any network it would import + call run_live_smoke;
        # poison it so the test fails loudly if the default path touches it.
        import scripts.smoke_live_double_line as mod

        def _boom(**_kwargs: object) -> object:
            raise AssertionError("run_live_smoke must NOT run without --real")

        monkeypatch.setattr(mod, "run_live_smoke", _boom)
        rc = main(["--json"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIPPED" in out

    def test_real_without_credentials_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in _DEBATE_AND_FEISHU:
            monkeypatch.delenv(name, raising=False)
        import scripts.smoke_live_double_line as mod

        monkeypatch.setattr(
            mod,
            "run_live_smoke",
            lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not run with missing creds")
            ),
        )
        # --real with no creds → exit 1, never invokes the network path.
        assert main(["--real"]) == 1


class TestSmokePingRenderer:
    def test_smoke_ping_is_fixed_literal_no_injection_surface(self) -> None:
        text = MessageRenderer().render_smoke_ping(
            sent_at=dt.datetime(2026, 5, 26, 1, 30, tzinfo=dt.UTC)
        )
        assert "连通性自检" in text
        assert "非交易指令" in text
        # Pilot banner only when requested.
        assert "试点" not in text
        piloted = MessageRenderer().render_smoke_ping(
            sent_at=dt.datetime(2026, 5, 26, 1, 30, tzinfo=dt.UTC), pilot=True
        )
        assert "试点" in piloted
