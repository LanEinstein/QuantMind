"""Tests for the H-001 secrets_validator (P1-6 §1.1 / P0-2-amendment-2026-05-16).

Coverage matrix:
* 5 应在 / 2 应缺 / 1 unknown legacy custom-bot (per task acceptance)
* fail-fast vs warning-only paths
* .env forbidden-prefix scanner (comments allowed, assignments rejected)
* SHA256[:8] fingerprint determinism + non-leakage
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.services.secrets_validator import (
    EXPECTED_POOL_SIZE,
    FEISHU_CREDENTIAL_NAMES,
    HETEROGENEOUS_CREDENTIAL_NAMES,
    LEGACY_FEISHU_CUSTOM_BOT_NAMES,
    LLM_API_KEY_NAMES,
    SecretsValidationError,
    SecretsValidator,
    _DeferredWarning,
    assert_secrets_or_exit,
    compute_fingerprint,
    dispatch_warnings_to_jsonl,
)

# Sample valid values matching each shape — long-enough to satisfy the
# regexes but obviously non-real.
VALID_LLM = "sk-" + "A" * 24
VALID_APP_ID = "cli_" + "a" * 16  # 20 chars total
VALID_SECRET32 = "x" * 32
VALID_CHAT_ID = "oc_" + "f" * 32  # 35 chars total


def _baseline_env() -> dict[str, str]:
    """Process env with all 3 LLM keys (feishu off by default)."""
    return {name: VALID_LLM for name in LLM_API_KEY_NAMES}


def _full_feishu_env() -> dict[str, str]:
    env = _baseline_env()
    env["FEISHU_INTERACTIVE_ENABLED"] = "true"
    env["FEISHU_APP_ID"] = VALID_APP_ID
    env["FEISHU_APP_SECRET"] = VALID_SECRET32
    env["FEISHU_VERIFY_TOKEN"] = VALID_SECRET32
    env["FEISHU_ENCRYPT_KEY"] = VALID_SECRET32
    env["FEISHU_ALERT_CHAT_ID"] = VALID_CHAT_ID
    return env


# -----------------------------------------------------------------------------
# Pool composition — P0-2-amendment-2026-05-16 closes the credential set
# -----------------------------------------------------------------------------


class TestPoolComposition:
    def test_total_pool_size_is_eight(self) -> None:
        assert len(LLM_API_KEY_NAMES) + len(FEISHU_CREDENTIAL_NAMES) == 8
        assert EXPECTED_POOL_SIZE == 8

    def test_custom_bot_names_are_legacy_not_required(self) -> None:
        assert "FEISHU_CUSTOM_BOT_WEBHOOK_URL" not in FEISHU_CREDENTIAL_NAMES
        assert "FEISHU_CUSTOM_BOT_SIGN_SECRET" not in FEISHU_CREDENTIAL_NAMES
        assert set(LEGACY_FEISHU_CUSTOM_BOT_NAMES) == {
            "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
            "FEISHU_CUSTOM_BOT_SIGN_SECRET",
        }


# -----------------------------------------------------------------------------
# LLM keys — always required regardless of FEISHU_INTERACTIVE_ENABLED
# -----------------------------------------------------------------------------


class TestLLMKeys:
    def test_three_keys_present_succeeds(self) -> None:
        result = SecretsValidator(env=_baseline_env()).validate()
        assert result.ok is True
        assert result.errors == ()
        assert set(result.fingerprints) == set(LLM_API_KEY_NAMES)
        for fp in result.fingerprints.values():
            assert len(fp) == 8

    def test_missing_one_llm_key_blocks(self) -> None:
        env = _baseline_env()
        env.pop("DEEPSEEK_API_KEY")
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any("DEEPSEEK_API_KEY" in e for e in result.errors)

    def test_missing_two_llm_keys_blocks_with_both_errors(self) -> None:
        env = _baseline_env()
        env.pop("DASHSCOPE_API_KEY")
        env.pop("MOONSHOT_API_KEY")
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert sum("DASHSCOPE_API_KEY" in e for e in result.errors) == 1
        assert sum("MOONSHOT_API_KEY" in e for e in result.errors) == 1

    def test_malformed_llm_key_shape_rejected(self) -> None:
        env = _baseline_env()
        env["DEEPSEEK_API_KEY"] = "not-an-sk-key"
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any(
            "DEEPSEEK_API_KEY" in e and "shape check" in e
            for e in result.errors
        )

    def test_blank_value_treated_as_missing(self) -> None:
        env = _baseline_env()
        env["MOONSHOT_API_KEY"] = "   "
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any(
            "MOONSHOT_API_KEY" in e and "missing" in e
            for e in result.errors
        )


# -----------------------------------------------------------------------------
# Feishu credentials — required only when FEISHU_INTERACTIVE_ENABLED=true
# -----------------------------------------------------------------------------


class TestFeishuCredentials:
    def test_feishu_off_does_not_require_feishu_creds(self) -> None:
        """5 应在 / 2 应缺 / 1 unknown matrix — Feishu off path."""
        env = _baseline_env()  # FEISHU_INTERACTIVE_ENABLED unset
        result = SecretsValidator(env=env).validate()
        assert result.ok is True
        # 飞书 5 凭证 not in fingerprint map when Feishu disabled
        assert set(result.fingerprints) == set(LLM_API_KEY_NAMES)

    def test_feishu_on_requires_all_five_creds(self) -> None:
        """5 应在 — Feishu on, all 5 present + valid shape."""
        result = SecretsValidator(env=_full_feishu_env()).validate()
        assert result.ok is True
        # 5 LLM + 5 Feishu - actually 3 LLM + 5 Feishu = 8
        assert len(result.fingerprints) == EXPECTED_POOL_SIZE
        for name in FEISHU_CREDENTIAL_NAMES:
            assert name in result.fingerprints

    def test_feishu_on_missing_one_cred_blocks(self) -> None:
        """5 应在 / 2 应缺 — Feishu on, 1 missing → fail-fast."""
        env = _full_feishu_env()
        env.pop("FEISHU_APP_SECRET")
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any("FEISHU_APP_SECRET" in e for e in result.errors)

    def test_feishu_on_missing_two_creds_blocks(self) -> None:
        env = _full_feishu_env()
        env.pop("FEISHU_VERIFY_TOKEN")
        env.pop("FEISHU_ALERT_CHAT_ID")
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any("FEISHU_VERIFY_TOKEN" in e for e in result.errors)
        assert any("FEISHU_ALERT_CHAT_ID" in e for e in result.errors)

    def test_feishu_app_id_shape_check(self) -> None:
        env = _full_feishu_env()
        env["FEISHU_APP_ID"] = "wrong_prefix_app_id__"  # 20 chars but wrong shape
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any(
            "FEISHU_APP_ID" in e and "shape check" in e
            for e in result.errors
        )

    def test_feishu_alert_chat_id_shape_check(self) -> None:
        env = _full_feishu_env()
        env["FEISHU_ALERT_CHAT_ID"] = "chat_" + "a" * 30  # not oc_
        result = SecretsValidator(env=env).validate()
        assert result.ok is False
        assert any(
            "FEISHU_ALERT_CHAT_ID" in e and "shape check" in e
            for e in result.errors
        )

    def test_truthy_aliases_for_feishu_flag(self) -> None:
        """``FEISHU_INTERACTIVE_ENABLED`` truthy variants all trigger the gate."""
        for token in ("true", "1", "yes", "on", "TRUE", "True"):
            env = _baseline_env()
            env["FEISHU_INTERACTIVE_ENABLED"] = token
            # 飞书 凭证 missing → must fail
            result = SecretsValidator(env=env).validate()
            assert result.ok is False, f"token={token!r} should trigger gate"

    def test_falsy_aliases_skip_feishu_gate(self) -> None:
        for token in ("false", "0", "no", "off", "", "   ", "garbage"):
            env = _baseline_env()
            env["FEISHU_INTERACTIVE_ENABLED"] = token
            result = SecretsValidator(env=env).validate()
            assert result.ok is True, f"token={token!r} should skip gate"


# -----------------------------------------------------------------------------
# 1 unknown legacy custom-bot — soft warning + audit, never blocks
# -----------------------------------------------------------------------------


class TestLegacyCustomBotSoftWarning:
    def test_legacy_webhook_url_emits_warning_not_error(self) -> None:
        """1 unknown legacy — warning only, startup proceeds."""
        env = _baseline_env()
        env["FEISHU_CUSTOM_BOT_WEBHOOK_URL"] = (
            "https://open.feishu.cn/open-apis/bot/v2/hook/dead-cafe-xxxx"
        )
        result = SecretsValidator(env=env).validate()
        assert result.ok is True
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert (
            warning.reason_namespace
            == "unexpected_legacy_feishu_custom_bot_credential"
        )
        assert warning.resource_id == "FEISHU_CUSTOM_BOT_WEBHOOK_URL"
        assert warning.payload["credential_name"] == (
            "FEISHU_CUSTOM_BOT_WEBHOOK_URL"
        )
        # Fingerprint, never plaintext.
        assert "fingerprint" in warning.payload
        assert len(warning.payload["fingerprint"]) == 8
        assert (
            warning.payload["amendment"] == "P0-2-amendment-2026-05-16"
        )

    def test_both_custom_bot_envs_emit_two_warnings(self) -> None:
        env = _baseline_env()
        env["FEISHU_CUSTOM_BOT_WEBHOOK_URL"] = "https://example/dead-cafe"
        env["FEISHU_CUSTOM_BOT_SIGN_SECRET"] = "legacy_sign_secret_xxxxxxxxx"
        result = SecretsValidator(env=env).validate()
        assert result.ok is True
        assert {w.resource_id for w in result.warnings} == set(
            LEGACY_FEISHU_CUSTOM_BOT_NAMES
        )

    def test_no_legacy_envs_emits_no_warnings(self) -> None:
        result = SecretsValidator(env=_baseline_env()).validate()
        assert result.warnings == ()

    def test_legacy_envs_do_not_appear_in_required_pool(self) -> None:
        """The amendment removed CUSTOM_BOT_* from the credential pool, so
        legacy presence never enlarges fingerprints."""
        env = _baseline_env()
        env["FEISHU_CUSTOM_BOT_WEBHOOK_URL"] = "https://example/dead-cafe"
        result = SecretsValidator(env=env).validate()
        assert (
            "FEISHU_CUSTOM_BOT_WEBHOOK_URL" not in result.fingerprints
        )


# -----------------------------------------------------------------------------
# Heterogeneous credentials — GITHUB_TOKEN soft-warn (codex X-027 R4 1+2)
# -----------------------------------------------------------------------------


class TestHeterogeneousCredentials:
    def test_github_token_listed_outside_canonical_pool(self) -> None:
        """Q3 keeps PAT heterogeneous — never count toward EXPECTED_POOL_SIZE."""
        assert "GITHUB_TOKEN" in HETEROGENEOUS_CREDENTIAL_NAMES
        assert "GITHUB_TOKEN" not in LLM_API_KEY_NAMES
        assert "GITHUB_TOKEN" not in FEISHU_CREDENTIAL_NAMES
        # Pool size stays 8 (3 LLM + 5 Feishu) regardless of PAT.
        assert EXPECTED_POOL_SIZE == 8

    def test_github_token_well_formed_prefix_no_warning(self) -> None:
        """ghp_ prefix + sufficient body — validator emits zero warnings."""
        env = _baseline_env()
        env["GITHUB_TOKEN"] = "ghp_" + "A" * 24  # well-formed classic PAT
        result = SecretsValidator(env=env).validate()
        assert result.ok is True
        for w in result.warnings:
            assert w.resource_id != "GITHUB_TOKEN"
        # And the heterogeneous credential must not enter fingerprints.
        assert "GITHUB_TOKEN" not in result.fingerprints

    def test_github_token_fine_grained_prefix_no_warning(self) -> None:
        """github_pat_ prefix (fine-grained) also accepted."""
        env = _baseline_env()
        env["GITHUB_TOKEN"] = "github_pat_" + "B" * 60
        result = SecretsValidator(env=env).validate()
        assert result.ok is True
        for w in result.warnings:
            assert w.resource_id != "GITHUB_TOKEN"

    def test_github_token_malformed_prefix_emits_soft_warning(self) -> None:
        """Misshapen PAT triggers a soft warning but never blocks startup."""
        env = _baseline_env()
        env["GITHUB_TOKEN"] = "xyz-not-a-github-pat"
        result = SecretsValidator(env=env).validate()
        assert result.ok is True  # warnings never block
        github_warnings = [
            w for w in result.warnings if w.resource_id == "GITHUB_TOKEN"
        ]
        assert len(github_warnings) == 1
        w = github_warnings[0]
        assert w.reason_namespace == "malformed_heterogeneous_credential"
        assert w.payload["credential_name"] == "GITHUB_TOKEN"
        # Fingerprint never leaks plaintext.
        assert len(w.payload["fingerprint"]) == 8
        assert "xyz-not-a-github-pat" not in w.payload["fingerprint"]

    def test_github_token_unset_emits_no_warning(self) -> None:
        """Absent env var is the production-wiring concern (crawler init
        fails fast there), not a validator warning."""
        env = _baseline_env()  # GITHUB_TOKEN unset
        result = SecretsValidator(env=env).validate()
        for w in result.warnings:
            assert w.resource_id != "GITHUB_TOKEN"

    def test_env_with_github_token_assignment_rejects(
        self, tmp_path: Path
    ) -> None:
        """Codex X-027 R4 cycle 1 P1 follow-up — the .env scan must
        also reject ``GITHUB_TOKEN=...`` since the docstring promise
        is "secrets live in ~/.bashrc, never in .env"."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GITHUB_TOKEN=ghp_leaked_into_env_file_xxxxxxxx\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is False
        assert any(
            "GITHUB_TOKEN" in e and ".env" in e for e in result.errors
        )


# -----------------------------------------------------------------------------
# .env scanner — credential assignments forbidden, comments allowed
# -----------------------------------------------------------------------------


class TestEnvFileScanner:
    def test_env_with_only_comments_passes(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# DEEPSEEK_API_KEY must live in ~/.bashrc\n"
            "MONGODB_URI=mongodb://127.0.0.1:27017/quantmind\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is True

    def test_env_with_llm_assignment_rejects(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "MONGODB_URI=mongodb://127.0.0.1:27017/quantmind\n"
            "DEEPSEEK_API_KEY=sk-leaked-into-env-file-xxxxxxxx\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is False
        assert any(
            "DEEPSEEK_API_KEY" in e and ".env" in e
            for e in result.errors
        )

    def test_env_with_feishu_app_id_assignment_rejects(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FEISHU_APP_ID=cli_leaked\n", encoding="utf-8")
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is False
        assert any("FEISHU_APP_ID" in e for e in result.errors)

    def test_env_with_custom_bot_assignment_rejects(
        self, tmp_path: Path
    ) -> None:
        """Even legacy CUSTOM_BOT_* assignments in .env are P1-6 §1.1 violations
        (the scanner is prefix-based; legacy soft-warning only applies to
        process env, never the .env file)."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "FEISHU_CUSTOM_BOT_WEBHOOK_URL=https://example\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is False
        assert any(
            "FEISHU_CUSTOM_BOT_WEBHOOK_URL" in e for e in result.errors
        )

    def test_missing_env_file_is_ok(self, tmp_path: Path) -> None:
        validator = SecretsValidator(
            env=_baseline_env(), env_file=tmp_path / "does-not-exist"
        )
        assert validator.validate().ok is True

    def test_indented_comment_still_allowed(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "  # DEEPSEEK_API_KEY documented in ~/.bashrc\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        assert validator.validate().ok is True

    def test_env_assignment_with_leading_whitespace_caught(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "   DASHSCOPE_API_KEY=sk-leaked-with-indent\n",
            encoding="utf-8",
        )
        validator = SecretsValidator(env=_baseline_env(), env_file=env_file)
        result = validator.validate()
        assert result.ok is False


# -----------------------------------------------------------------------------
# Fingerprint helper
# -----------------------------------------------------------------------------


class TestFingerprint:
    def test_fingerprint_is_eight_hex(self) -> None:
        fp = compute_fingerprint("sk-deadbeef-aaaaaaaaaaaaaaaa")
        assert len(fp) == 8
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_is_deterministic(self) -> None:
        assert compute_fingerprint("hello") == compute_fingerprint("hello")

    def test_different_values_yield_different_fingerprints(self) -> None:
        a = compute_fingerprint("sk-AAAAAAAAAAAAAAAA")
        b = compute_fingerprint("sk-BBBBBBBBBBBBBBBB")
        assert a != b

    def test_fingerprint_never_leaks_plaintext(self) -> None:
        secret = "sk-PLAINTEXT_SHOULD_NEVER_APPEAR"
        fp = compute_fingerprint(secret)
        assert secret not in fp
        assert "PLAINTEXT" not in fp

    def test_fingerprint_matches_sha256_prefix(self) -> None:
        secret = "qwerty"
        expected = hashlib.sha256(b"qwerty").hexdigest()[:8]
        assert compute_fingerprint(secret) == expected


# -----------------------------------------------------------------------------
# Convenience wrapper — assert_secrets_or_exit
# -----------------------------------------------------------------------------


class TestAssertOrExit:
    def test_happy_path_returns_result(self) -> None:
        result = assert_secrets_or_exit(
            SecretsValidator(env=_baseline_env())
        )
        assert result.ok is True

    def test_blocks_with_system_exit_on_error(self) -> None:
        env = _baseline_env()
        env.pop("DEEPSEEK_API_KEY")
        with pytest.raises(SecretsValidationError) as excinfo:
            assert_secrets_or_exit(SecretsValidator(env=env))
        assert any(
            "DEEPSEEK_API_KEY" in e for e in excinfo.value.errors
        )

    def test_error_subclass_of_system_exit(self) -> None:
        env = _baseline_env()
        env.pop("DEEPSEEK_API_KEY")
        with pytest.raises(SystemExit):
            assert_secrets_or_exit(SecretsValidator(env=env))

    def test_warnings_present_does_not_block(
        self, tmp_path: Path
    ) -> None:
        env = _baseline_env()
        env["FEISHU_CUSTOM_BOT_WEBHOOK_URL"] = "https://example/dead"
        # Should not raise
        result = assert_secrets_or_exit(
            SecretsValidator(env=env),
            audit_jsonl_path=tmp_path / "audit.jsonl",
        )
        assert result.ok is True
        assert len(result.warnings) == 1

    def test_soft_warning_writes_audit_jsonl(
        self, tmp_path: Path
    ) -> None:
        env = _baseline_env()
        env["FEISHU_CUSTOM_BOT_WEBHOOK_URL"] = "https://example/dead"
        audit_path = tmp_path / "audit.jsonl"
        assert_secrets_or_exit(
            SecretsValidator(env=env), audit_jsonl_path=audit_path
        )
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        import json as _json

        record = _json.loads(lines[0])
        assert record["event_type"] == "system_interrupted"
        assert record["actor"] == "system"
        assert record["outcome"] == "degraded"
        assert (
            record["reason_namespace"]
            == "unexpected_legacy_feishu_custom_bot_credential"
        )
        assert (
            record["resource_id"] == "FEISHU_CUSTOM_BOT_WEBHOOK_URL"
        )
        # Fingerprint flows through, plaintext does not.
        assert len(record["payload"]["fingerprint"]) == 8
        assert "example/dead" not in lines[0]


# -----------------------------------------------------------------------------
# dispatch_warnings_to_jsonl — direct helper
# -----------------------------------------------------------------------------


class TestDispatchWarningsToJsonl:
    def test_empty_warnings_writes_no_lines(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "audit.jsonl"
        assert dispatch_warnings_to_jsonl((), jsonl_path=path) == 0
        assert not path.exists()

    def test_warning_event_uses_locked_audit_shape(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "audit.jsonl"
        warning = _DeferredWarning(
            reason_namespace="unexpected_legacy_feishu_custom_bot_credential",
            resource_type="credential",
            resource_id="FEISHU_CUSTOM_BOT_SIGN_SECRET",
            payload={
                "credential_name": "FEISHU_CUSTOM_BOT_SIGN_SECRET",
                "fingerprint": "deadbeef",
                "amendment": "P0-2-amendment-2026-05-16",
            },
        )
        written = dispatch_warnings_to_jsonl((warning,), jsonl_path=path)
        assert written == 1
        import json as _json

        record = _json.loads(path.read_text(encoding="utf-8").strip())
        assert record["actor_detail"] == "secrets_validator"
        assert record["payload"]["amendment"] == "P0-2-amendment-2026-05-16"

    def test_dispatch_appends_not_overwrites(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "audit.jsonl"
        path.write_text("preexisting line\n", encoding="utf-8")
        warning = _DeferredWarning(
            reason_namespace="unexpected_legacy_feishu_custom_bot_credential",
            resource_type="credential",
            resource_id="FEISHU_CUSTOM_BOT_WEBHOOK_URL",
            payload={
                "credential_name": "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
                "fingerprint": "01234567",
                "amendment": "P0-2-amendment-2026-05-16",
            },
        )
        dispatch_warnings_to_jsonl((warning,), jsonl_path=path)
        body = path.read_text(encoding="utf-8")
        assert body.startswith("preexisting line\n")
        assert body.count("\n") == 2
