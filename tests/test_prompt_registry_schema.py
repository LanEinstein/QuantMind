"""X-001 unit tests — PromptLockFile / PromptAgentLock / PromptVersionEntry.

Schema-only tests. The loader / alias resolver lands in X-003.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.services.prompt_registry import (
    MANDATORY_AGENTS,
    PROMPT_ALIAS_NAMES,
    SHA256_HEX_RE,
    VERSION_TAG_RE,
    PromptAgentLock,
    PromptLockFile,
    PromptVersionEntry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_SHA = "a" * 64
VALID_NOW = datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_mandatory_agents_is_the_four_p010_agents() -> None:
    assert MANDATORY_AGENTS == frozenset(
        {
            "fundamental_analyst",
            "technical_analyst",
            "risk_officer",
            "fund_manager",
        }
    )


def test_alias_names_locked_three() -> None:
    assert PROMPT_ALIAS_NAMES == frozenset({"production", "staging", "challenger"})


def test_version_tag_regex_accepts_v1_v10_v1_0() -> None:
    for ok in ("v1", "v10", "v1.0", "v23.456"):
        assert VERSION_TAG_RE.fullmatch(ok) is not None, ok


def test_version_tag_regex_rejects_bad_forms() -> None:
    for bad in ("V1", "1", "v", "v1-rc1", "v1.0.0", "v1a", "ver1"):
        assert VERSION_TAG_RE.fullmatch(bad) is None, bad


def test_sha256_regex_locked_lowercase_hex_64() -> None:
    assert SHA256_HEX_RE.fullmatch("0" * 64) is not None
    assert SHA256_HEX_RE.fullmatch("A" * 64) is None  # uppercase rejected
    assert SHA256_HEX_RE.fullmatch("a" * 63) is None
    assert SHA256_HEX_RE.fullmatch("a" * 65) is None


# ---------------------------------------------------------------------------
# PromptVersionEntry
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    path: str = "config/prompts/fundamental_analyst/v1.yaml",
    sha256: str = VALID_SHA,
    pinned_at: datetime = VALID_NOW,
    pinned_by: str = "dr.zhang.xjtu@gmail.com",
) -> PromptVersionEntry:
    return PromptVersionEntry(
        path=path,
        sha256=sha256,
        pinned_at=pinned_at,
        pinned_by=pinned_by,
    )


def test_version_entry_happy_path() -> None:
    entry = _make_entry()
    assert entry.path.endswith(".yaml")
    assert entry.sha256 == VALID_SHA


def test_version_entry_is_frozen() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        entry.path = "other.yaml"  # type: ignore[misc]


def test_version_entry_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        PromptVersionEntry(
            path="config/prompts/fundamental_analyst/v1.yaml",
            sha256=VALID_SHA,
            pinned_at=VALID_NOW,
            pinned_by="owner",
            note="not allowed",  # type: ignore[call-arg]
        )


def test_version_entry_rejects_path_outside_config_prompts() -> None:
    with pytest.raises(ValidationError):
        _make_entry(path="other/dir/v1.yaml")


def test_version_entry_rejects_path_traversal_in_components() -> None:
    # Codex X-027 R4 claim 10: prefix-only ``startswith("config/prompts/")``
    # accepts paths whose components escape the prompts subtree.
    with pytest.raises(ValidationError, match="path-traversal"):
        _make_entry(path="config/prompts/../etc/passwd.yaml")


def test_version_entry_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        _make_entry(path="/etc/passwd.yaml")


def test_version_entry_rejects_backslash_path() -> None:
    with pytest.raises(ValidationError):
        _make_entry(path="config/prompts\\fundamental_analyst\\v1.yaml")


def test_version_entry_rejects_non_yaml_extension() -> None:
    with pytest.raises(ValidationError):
        _make_entry(path="config/prompts/fundamental_analyst/v1.json")


def test_version_entry_rejects_uppercase_sha() -> None:
    with pytest.raises(ValidationError):
        _make_entry(sha256="A" * 64)


def test_version_entry_rejects_short_sha() -> None:
    with pytest.raises(ValidationError):
        _make_entry(sha256="a" * 63)


# ---------------------------------------------------------------------------
# PromptAgentLock
# ---------------------------------------------------------------------------


def test_agent_lock_empty_is_valid() -> None:
    lock = PromptAgentLock()
    assert lock.aliases == {}
    assert lock.versions == {}


def test_agent_lock_alias_must_point_at_known_version() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        PromptAgentLock(
            aliases={"production": "v2"},
            versions={"v1": entry},
        )


def test_agent_lock_alias_target_must_match_version_regex() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        PromptAgentLock(
            aliases={"production": "bad-tag"},
            versions={"v1": entry},
        )


def test_agent_lock_version_tag_must_match_regex() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        PromptAgentLock(versions={"bad-tag": entry})


def test_agent_lock_full_happy_path() -> None:
    entry_v1 = _make_entry(sha256="b" * 64)
    entry_v2 = _make_entry(
        path="config/prompts/fundamental_analyst/v2.yaml",
        sha256="c" * 64,
    )
    lock = PromptAgentLock(
        aliases={"production": "v1", "staging": "v2"},
        versions={"v1": entry_v1, "v2": entry_v2},
    )
    assert lock.aliases["production"] == "v1"
    assert lock.versions["v2"].path.endswith("v2.yaml")


def test_agent_lock_unknown_alias_name_rejected_by_type() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        PromptAgentLock(
            aliases={"prod": "v1"},  # type: ignore[dict-item]
            versions={"v1": entry},
        )


# ---------------------------------------------------------------------------
# PromptLockFile
# ---------------------------------------------------------------------------


def test_lock_file_empty_agents_valid_for_bootstrap() -> None:
    lock = PromptLockFile(version="1.0", updated_at=VALID_NOW, agents={})
    assert lock.has_full_production_coverage() is False


def test_lock_file_rejects_unknown_agent_name() -> None:
    with pytest.raises(ValidationError) as exc:
        PromptLockFile(
            version="1.0",
            updated_at=VALID_NOW,
            agents={"news_crawler": PromptAgentLock()},
        )
    assert "unknown agent" in str(exc.value)


def test_lock_file_full_coverage_requires_production_for_all_four() -> None:
    entry = _make_entry()
    one_full = PromptAgentLock(
        aliases={"production": "v1"}, versions={"v1": entry}
    )
    only_one = PromptLockFile(
        version="1.0",
        updated_at=VALID_NOW,
        agents={"fundamental_analyst": one_full},
    )
    assert only_one.has_full_production_coverage() is False

    entries = {
        name: PromptAgentLock(
            aliases={"production": "v1"},
            versions={
                "v1": _make_entry(
                    path=f"config/prompts/{name}/v1.yaml",
                    sha256=VALID_SHA,
                )
            },
        )
        for name in MANDATORY_AGENTS
    }
    full = PromptLockFile(version="1.0", updated_at=VALID_NOW, agents=entries)
    assert full.has_full_production_coverage() is True


def test_lock_file_version_must_be_exactly_1_0() -> None:
    with pytest.raises(ValidationError):
        PromptLockFile(
            version="2.0",  # type: ignore[arg-type]
            updated_at=VALID_NOW,
            agents={},
        )


def test_lock_file_is_frozen() -> None:
    lock = PromptLockFile(version="1.0", updated_at=VALID_NOW, agents={})
    with pytest.raises(ValidationError):
        lock.version = "1.0"  # type: ignore[misc]


def test_lock_file_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        PromptLockFile(
            version="1.0",
            updated_at=VALID_NOW,
            agents={},
            stray_field="nope",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Repository invariants — config/prompts/ structure + prompts.lock.json
# ---------------------------------------------------------------------------


def test_config_prompts_has_four_mandatory_agent_subdirs() -> None:
    base = REPO_ROOT / "config" / "prompts"
    assert base.is_dir(), f"missing {base}"
    for agent in MANDATORY_AGENTS:
        agent_dir = base / agent
        assert agent_dir.is_dir(), f"missing {agent_dir}"
        assert (agent_dir / ".gitkeep").exists(), (
            f".gitkeep placeholder missing in {agent_dir}"
        )


def test_prompts_lock_json_parses_with_schema() -> None:
    lock_path = REPO_ROOT / "config" / "prompts.lock.json"
    assert lock_path.is_file(), f"missing {lock_path}"
    raw = lock_path.read_text(encoding="utf-8")
    # Sanity-check the raw JSON is parseable independently of the model.
    parsed = json.loads(raw)
    assert parsed["version"] == "1.0"
    # ``model_validate_json`` performs JSON-mode coercion (e.g.
    # ISO-8601 strings -> datetime) which the stricter ``model_validate``
    # path intentionally refuses.
    lock = PromptLockFile.model_validate_json(raw)
    assert lock.version == "1.0"
    assert lock.agents == {}
    assert lock.has_full_production_coverage() is False
