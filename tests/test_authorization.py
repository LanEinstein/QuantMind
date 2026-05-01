"""Tests for backend.services.authorization phase/mode gating."""

from __future__ import annotations

import pytest

from backend.services.authorization import (
    ALLOWED_MODES_BY_PHASE,
    CANONICAL_MODES,
    CrossPhaseAuthorizationError,
    assert_authorization_mode,
    assert_mode_allowed_for_phase,
    current_mode,
    current_phase,
    normalize_mode,
)


# ---------------------------------------------------------------------------
# normalize_mode
# ---------------------------------------------------------------------------


class TestNormalizeMode:
    """Map both short and legacy long forms onto the canonical short."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("suggest", "suggest"),
            ("confirm", "confirm"),
            ("auto", "auto"),
            ("suggestion", "suggest"),
            ("semi_auto", "confirm"),
            ("full_auto", "auto"),
        ],
    )
    def test_known_aliases(self, raw: str, expected: str) -> None:
        assert normalize_mode(raw) == expected

    def test_strip_and_lowercase(self) -> None:
        assert normalize_mode("  Suggestion ") == "suggest"
        assert normalize_mode("FULL_AUTO") == "auto"

    def test_unknown_passes_through(self) -> None:
        # The caller is expected to treat the canonical-set membership
        # check as the validation, not normalize_mode.
        assert normalize_mode("garbage") == "garbage"


# ---------------------------------------------------------------------------
# current_phase / current_mode
# ---------------------------------------------------------------------------


class TestEnvAccessors:
    def test_default_phase_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("QUANTMIND_PHASE", raising=False)
        assert current_phase() == "phase5_eval"

    def test_phase_lowercased(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "Phase6_Dryrun")
        assert current_phase() == "phase6_dryrun"

    def test_default_mode_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTHORIZATION_MODE", raising=False)
        assert current_mode() == "suggest"

    def test_mode_normalized_from_long_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTHORIZATION_MODE", "semi_auto")
        assert current_mode() == "confirm"


# ---------------------------------------------------------------------------
# assert_authorization_mode (startup)
# ---------------------------------------------------------------------------


class TestAssertAuthorizationMode:
    """Startup gate must SystemExit on any mismatch."""

    def test_passes_for_default_phase_and_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        monkeypatch.setenv("AUTHORIZATION_MODE", "suggest")
        phase, mode = assert_authorization_mode()
        assert phase == "phase5_eval"
        assert mode == "suggest"

    def test_passes_when_mode_uses_legacy_long_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        monkeypatch.setenv("AUTHORIZATION_MODE", "suggestion")
        phase, mode = assert_authorization_mode()
        # Returned mode is the canonical short form, not the input.
        assert mode == "suggest"

    @pytest.mark.parametrize("bad_mode", ["confirm", "auto", "semi_auto", "full_auto"])
    def test_rejects_cross_phase_in_eval(
        self, monkeypatch: pytest.MonkeyPatch, bad_mode: str
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        monkeypatch.setenv("AUTHORIZATION_MODE", bad_mode)
        with pytest.raises(SystemExit) as ctx:
            assert_authorization_mode()
        # Error must name the violated knob loudly so an operator
        # reading systemd logs can fix without diving into code.
        assert "AUTHORIZATION_MODE" in str(ctx.value)
        assert "phase5_eval" in str(ctx.value)

    def test_rejects_unknown_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase42_doom")
        monkeypatch.setenv("AUTHORIZATION_MODE", "suggest")
        with pytest.raises(SystemExit) as ctx:
            assert_authorization_mode()
        assert "QUANTMIND_PHASE" in str(ctx.value)

    def test_rejects_garbage_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        monkeypatch.setenv("AUTHORIZATION_MODE", "yolo_mode")
        with pytest.raises(SystemExit) as ctx:
            assert_authorization_mode()
        assert "AUTHORIZATION_MODE" in str(ctx.value)

    def test_phase6_dryrun_allows_confirm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase6_dryrun")
        monkeypatch.setenv("AUTHORIZATION_MODE", "confirm")
        phase, mode = assert_authorization_mode()
        assert phase == "phase6_dryrun"
        assert mode == "confirm"

    def test_phase7_live_allows_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase7_live")
        monkeypatch.setenv("AUTHORIZATION_MODE", "auto")
        phase, mode = assert_authorization_mode()
        assert phase == "phase7_live"
        assert mode == "auto"


# ---------------------------------------------------------------------------
# assert_mode_allowed_for_phase (API guard)
# ---------------------------------------------------------------------------


class TestAssertModeAllowedForPhase:
    """API endpoint guard returns canonical short form on success."""

    def test_returns_canonical_for_short_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        assert assert_mode_allowed_for_phase("suggest") == "suggest"

    def test_returns_canonical_for_long_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        assert assert_mode_allowed_for_phase("suggestion") == "suggest"

    def test_rejects_cross_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        with pytest.raises(CrossPhaseAuthorizationError):
            assert_mode_allowed_for_phase("auto")

    def test_rejects_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        with pytest.raises(CrossPhaseAuthorizationError):
            assert_mode_allowed_for_phase("nope")

    def test_explicit_phase_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        # Explicit phase argument wins so testing future phases is easy.
        result = assert_mode_allowed_for_phase("auto", phase="phase7_live")
        assert result == "auto"

    def test_rejects_unknown_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        with pytest.raises(CrossPhaseAuthorizationError):
            assert_mode_allowed_for_phase("suggest", phase="phase42_doom")


# ---------------------------------------------------------------------------
# Static invariants
# ---------------------------------------------------------------------------


class TestPhaseLedgerInvariants:
    """The plan §2.9 redline matrix."""

    def test_phase5_and_phase6_prep_only_suggest(self) -> None:
        assert ALLOWED_MODES_BY_PHASE["phase5_eval"] == frozenset({"suggest"})
        assert ALLOWED_MODES_BY_PHASE["phase6_prep"] == frozenset({"suggest"})

    def test_phase6_dryrun_excludes_auto(self) -> None:
        allowed = ALLOWED_MODES_BY_PHASE["phase6_dryrun"]
        assert "auto" not in allowed
        assert {"suggest", "confirm"} <= allowed

    def test_phase7_live_includes_auto(self) -> None:
        assert ALLOWED_MODES_BY_PHASE["phase7_live"] == frozenset(
            {"suggest", "confirm", "auto"}
        )

    def test_canonical_modes_complete(self) -> None:
        # Every value in any phase allow-list must be a canonical mode.
        for allowed in ALLOWED_MODES_BY_PHASE.values():
            assert allowed <= CANONICAL_MODES
