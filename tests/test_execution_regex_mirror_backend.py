"""G-005 — Backend half of the shared regex-mirror fixture.

Pair file: ``frontend/src/utils/__tests__/executionRegex.spec.ts``.

Both run against the same JSON fixture so a single-side edit lights
up immediately. P0-4 §1.1 forbids LLM-assisted parsing — the regex
tier is the only allowed parser shape, so the mirror MUST stay in
sync (drift means the frontend preview would lie to the operator).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.execution.regex_patterns import (
    PATTERNS_AS_DICT,
    R_AMEND_FILLED,
    R_AMEND_PARTIAL,
    R_AMEND_UNFILLED,
    R_FILLED,
    R_PARTIAL,
    R_POST_CLOSE_FILLED,
    R_POST_CLOSE_PARTIAL,
    R_POST_CLOSE_UNFILLED,
    R_UNFILLED,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "execution_reports_mirror_samples.json"
)


_BY_ID: dict[str, re.Pattern[str]] = {
    "FILLED": R_FILLED,
    "PARTIAL": R_PARTIAL,
    "UNFILLED": R_UNFILLED,
    "AMEND_FILLED": R_AMEND_FILLED,
    "AMEND_PARTIAL": R_AMEND_PARTIAL,
    "AMEND_UNFILLED": R_AMEND_UNFILLED,
    "POST_CLOSE_FILLED": R_POST_CLOSE_FILLED,
    "POST_CLOSE_PARTIAL": R_POST_CLOSE_PARTIAL,
    "POST_CLOSE_UNFILLED": R_POST_CLOSE_UNFILLED,
}


@pytest.fixture(scope="module")
def fixture() -> dict[str, list[dict[str, object]]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_covers_all_nine_pattern_ids(
    fixture: dict[str, list[dict[str, object]]],
) -> None:
    covered = {sample["pattern_id"] for sample in fixture["valid"]}
    assert covered == set(PATTERNS_AS_DICT.keys()), (
        f"fixture missing pattern_ids; covered={covered} "
        f"expected={set(PATTERNS_AS_DICT.keys())}"
    )


def test_valid_samples_match_expected_pattern_and_groups(
    fixture: dict[str, list[dict[str, object]]],
) -> None:
    for sample in fixture["valid"]:
        pattern_id = sample["pattern_id"]
        raw = sample["raw_text"]
        expected = sample["groups"]
        regex = _BY_ID[pattern_id]
        match = regex.fullmatch(raw)
        assert match is not None, (
            f"sample {sample['name']!r} should match {pattern_id} "
            f"but no pattern matched"
        )
        for key, value in expected.items():
            assert match.group(key) == value, (
                f"sample {sample['name']!r} pattern={pattern_id} "
                f"group={key} got={match.group(key)!r} "
                f"expected={value!r}"
            )


def test_valid_samples_match_no_other_pattern(
    fixture: dict[str, list[dict[str, object]]],
) -> None:
    """Each sample must match exactly ONE pattern (no ambiguity)."""
    for sample in fixture["valid"]:
        raw = sample["raw_text"]
        matches = [pid for pid, rx in _BY_ID.items() if rx.fullmatch(raw)]
        assert matches == [sample["pattern_id"]], (
            f"sample {sample['name']!r} matched {matches}; "
            f"expected exactly [{sample['pattern_id']!r}]"
        )


def test_invalid_samples_reject_all_patterns(
    fixture: dict[str, list[dict[str, object]]],
) -> None:
    for sample in fixture["invalid"]:
        raw = sample["raw_text"]
        matches = [pid for pid, rx in _BY_ID.items() if rx.fullmatch(raw)]
        assert matches == [], (
            f"invalid sample {sample['name']!r} unexpectedly matched {matches}"
        )


def test_pattern_keys_locked() -> None:
    """The 9 keys + their order are the public mirror contract."""
    assert list(PATTERNS_AS_DICT.keys()) == [
        "FILLED",
        "PARTIAL",
        "UNFILLED",
        "AMEND_FILLED",
        "AMEND_PARTIAL",
        "AMEND_UNFILLED",
        "POST_CLOSE_FILLED",
        "POST_CLOSE_PARTIAL",
        "POST_CLOSE_UNFILLED",
    ]


def test_frontend_mirror_uses_same_keys() -> None:
    """The TS file must export PATTERN_IDS with the same 9 strings."""
    mirror = Path("frontend/src/utils/executionRegex.ts").read_text(
        encoding="utf-8"
    )
    for key in PATTERNS_AS_DICT.keys():
        assert f"'{key}'" in mirror, (
            f"frontend mirror missing pattern id {key!r}"
        )


# F2 (production-hardening 2026-06-25): byte-equal pattern-STRING mirror.
# The sample-based tests above only prove BEHAVIOURAL equivalence on the fixture
# rows — a one-sided edit to a pattern string that still happens to match every
# sample would pass both suites. This generated artifact is the bridge: this
# backend test asserts it byte-matches the (JS-normalized) backend SSoT, and the
# vitest spec asserts the TS PATTERN_STRINGS byte-match the SAME artifact — so
# any single-side string edit breaks exactly one of the two suites.
NORMALIZED_ARTIFACT_PATH = (
    Path("frontend/src/utils/__tests__/execution_regex_patterns.normalized.json")
)


def _js_normalize(pattern: str) -> str:
    """Python named groups ``(?P<name>)`` → JS named groups ``(?<name>)``.

    The ONLY syntactic difference between the anchored Python pattern strings
    and the TS mirror; everything else must be byte-identical.
    """
    return pattern.replace("(?P<", "(?<")


def test_normalized_pattern_artifact_matches_backend_ssot() -> None:
    artifact = json.loads(NORMALIZED_ARTIFACT_PATH.read_text(encoding="utf-8"))
    expected = {
        pid: _js_normalize(pattern) for pid, pattern in PATTERNS_AS_DICT.items()
    }
    assert artifact == expected, (
        "execution_regex_patterns.normalized.json is stale — regenerate it from "
        "backend.execution.regex_patterns.PATTERNS_AS_DICT (replace '(?P<'→'(?<') "
        "in the SAME commit that edits the backend patterns."
    )
