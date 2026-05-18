"""X-003 unit tests — PromptRegistry loader / alias / checksum / fail-fast.

Schema tests live in ``test_prompt_registry_schema.py`` (X-001). This
module focuses on the boot-time loader and the immutability /
fail-fast invariants introduced by X-003.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.services.prompt_registry import (
    MANDATORY_AGENTS,
    PromptAgentCoverageError,
    PromptChecksumMismatchError,
    PromptFileNotFoundError,
    PromptLockFileMalformedError,
    PromptLockFileNotFoundError,
    PromptRegistry,
    compute_prompt_sha256,
)

# ---------------------------------------------------------------------------
# Fixtures — build a self-contained repo skeleton under tmp_path
# ---------------------------------------------------------------------------


def _write_prompt(
    repo: Path,
    agent: str,
    version: str,
    content: str,
) -> str:
    """Write ``config/prompts/{agent}/{version}.yaml`` and return its SHA."""
    target = repo / "config" / "prompts" / agent / f"{version}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    target.write_bytes(payload)
    return compute_prompt_sha256(payload)


def _write_lock(
    repo: Path,
    agents: dict[str, dict[str, object]],
) -> Path:
    """Write ``config/prompts.lock.json`` with the supplied agents map."""
    lock_path = repo / "config" / "prompts.lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "updated_at": datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC).isoformat(),
        "agents": agents,
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    return lock_path


def _bootstrap_full_registry(repo: Path) -> Path:
    """Pin a minimal v1 prompt for every mandatory agent and write the lock."""
    agents_payload: dict[str, dict[str, object]] = {}
    for agent in MANDATORY_AGENTS:
        sha = _write_prompt(repo, agent, "v1", f"# {agent} v1 prompt\n")
        agents_payload[agent] = {
            "aliases": {"production": "v1"},
            "versions": {
                "v1": {
                    "path": f"config/prompts/{agent}/v1.yaml",
                    "sha256": sha,
                    "pinned_at": datetime(
                        2026, 5, 18, 0, 0, 0, tzinfo=UTC
                    ).isoformat(),
                    "pinned_by": "dr.zhang.xjtu@gmail.com",
                },
            },
        }
    return _write_lock(repo, agents_payload)


# ---------------------------------------------------------------------------
# compute_prompt_sha256 — canonical hash function
# ---------------------------------------------------------------------------


def test_compute_prompt_sha256_known_value() -> None:
    # echo -n "hello\n" | sha256sum
    assert compute_prompt_sha256(b"hello\n") == (
        "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


def test_compute_prompt_sha256_empty_bytes() -> None:
    assert compute_prompt_sha256(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_from_lockfile_happy_path_loads_every_mandatory_agent(
    tmp_path: Path,
) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.agents == frozenset(MANDATORY_AGENTS)
    for agent in MANDATORY_AGENTS:
        assert registry.resolve_alias(agent, "production") == "v1"
        content = registry.get_prompt(agent, "production")
        assert agent in content
        assert content.endswith("v1 prompt\n")


def test_get_prompt_accepts_version_tag_directly(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.get_prompt("fundamental_analyst", "v1").endswith(
        "v1 prompt\n"
    )


def test_list_aliases_and_versions(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.list_versions("fundamental_analyst") == ("v1",)
    assert registry.list_aliases("fundamental_analyst") == ("production",)


def test_has_full_production_coverage_true_when_all_pinned(
    tmp_path: Path,
) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.has_full_production_coverage() is True


def test_has_full_production_coverage_false_when_empty(tmp_path: Path) -> None:
    lock_path = _write_lock(tmp_path, agents={})
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.has_full_production_coverage() is False


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_registry_setattr_blocked(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    with pytest.raises(AttributeError, match="immutable"):
        registry.lock = registry.lock  # type: ignore[misc]


def test_registry_contents_view_is_proxy(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    # Internal mapping is wrapped in MappingProxyType — direct mutation
    # raises TypeError, protecting the loaded prompts from drift.
    with pytest.raises(TypeError):
        registry._contents["new"] = "bad"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Fail-fast errors
# ---------------------------------------------------------------------------


def test_missing_lock_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptLockFileNotFoundError):
        PromptRegistry.from_lockfile(
            tmp_path / "no-such-lock.json", repo_root=tmp_path
        )


def test_malformed_lock_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "lock.json"
    bad.write_text("this is not json", encoding="utf-8")
    with pytest.raises(PromptLockFileMalformedError):
        PromptRegistry.from_lockfile(bad, repo_root=tmp_path)


def test_schema_violation_raises_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "lock.json"
    bad.write_text(
        json.dumps(
            {
                "version": "2.0",  # version literal locked to 1.0
                "updated_at": "2026-05-18T00:00:00+00:00",
                "agents": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptLockFileMalformedError):
        PromptRegistry.from_lockfile(bad, repo_root=tmp_path)


def test_missing_prompt_yaml_raises(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    target = tmp_path / "config/prompts/fundamental_analyst/v1.yaml"
    target.unlink()
    with pytest.raises(PromptFileNotFoundError, match="fundamental_analyst"):
        PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_checksum_mismatch_raises(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    # Mutate one prompt out-of-band so the SHA shifts.
    target = tmp_path / "config/prompts/risk_officer/v1.yaml"
    target.write_text("# tampered content\n", encoding="utf-8")
    with pytest.raises(PromptChecksumMismatchError, match="sha256 mismatch"):
        PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_require_full_production_coverage_raises_when_partial(
    tmp_path: Path,
) -> None:
    # Pin only one of the four mandatory agents.
    sha = _write_prompt(
        tmp_path, "fundamental_analyst", "v1", "# only one pinned\n"
    )
    lock_path = _write_lock(
        tmp_path,
        agents={
            "fundamental_analyst": {
                "aliases": {"production": "v1"},
                "versions": {
                    "v1": {
                        "path": "config/prompts/fundamental_analyst/v1.yaml",
                        "sha256": sha,
                        "pinned_at": datetime(
                            2026, 5, 18, 0, 0, 0, tzinfo=UTC
                        ).isoformat(),
                        "pinned_by": "dr.zhang.xjtu@gmail.com",
                    }
                },
            }
        },
    )
    with pytest.raises(PromptAgentCoverageError, match="production alias"):
        PromptRegistry.from_lockfile(
            lock_path, repo_root=tmp_path,
            require_full_production_coverage=True,
        )


def test_require_full_production_coverage_passes_when_all_pinned(
    tmp_path: Path,
) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(
        lock_path,
        repo_root=tmp_path,
        require_full_production_coverage=True,
    )
    assert registry.has_full_production_coverage() is True


# ---------------------------------------------------------------------------
# Alias semantics
# ---------------------------------------------------------------------------


def test_resolve_alias_missing_raises_keyerror(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    with pytest.raises(KeyError, match="challenger"):
        registry.resolve_alias("fundamental_analyst", "challenger")


def test_resolve_alias_unknown_agent_returns_empty_aliases(
    tmp_path: Path,
) -> None:
    lock_path = _write_lock(tmp_path, agents={})
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    assert registry.list_aliases("fundamental_analyst") == ()


def test_get_prompt_unknown_agent_raises(tmp_path: Path) -> None:
    lock_path = _write_lock(tmp_path, agents={})
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    with pytest.raises(KeyError, match="not present in lockfile"):
        registry.get_prompt("fundamental_analyst", "production")


def test_get_prompt_unknown_version_raises(tmp_path: Path) -> None:
    lock_path = _bootstrap_full_registry(tmp_path)
    registry = PromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)
    with pytest.raises(KeyError, match="no content registered"):
        registry.get_prompt("fundamental_analyst", "v999")


# ---------------------------------------------------------------------------
# Direct constructor (test substrate, not a public path)
# ---------------------------------------------------------------------------


def test_direct_constructor_supports_test_doubles() -> None:
    """The two-arg constructor lets tests build registries without IO."""
    from backend.services.prompt_registry import (
        PromptAgentLock,
        PromptLockFile,
        PromptVersionEntry,
    )

    entry = PromptVersionEntry(
        path="config/prompts/fundamental_analyst/v1.yaml",
        sha256="d" * 64,
        pinned_at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC),
        pinned_by="owner",
    )
    lock = PromptLockFile(
        version="1.0",
        updated_at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC),
        agents={
            "fundamental_analyst": PromptAgentLock(
                aliases={"production": "v1"}, versions={"v1": entry}
            )
        },
    )
    registry = PromptRegistry(
        lock,
        contents={("fundamental_analyst", "v1"): "# in-memory prompt"},
    )
    assert registry.get_prompt("fundamental_analyst", "production").endswith(
        "in-memory prompt"
    )
