"""X-004 unit tests — RagProvenanceEntry / SanitizationApplied / verifier.

Schema-level invariants + the hash-anchored citation guarantee that
the X-013 amendment drafter relies on. Builds on the X-002 writer
skeleton tests in ``test_provenance_writer.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.evolution.provenance import (
    DOC_ID_RE,
    WHITELIST_RULE_VERSION_RE,
    WHITELIST_SOURCES,
    ProvenanceVerifier,
    ProvenanceVerifierError,
    ProvenanceWriter,
    RagProvenanceEntry,
    SanitizationApplied,
    compute_content_sha256,
)

VALID_NOW = datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC)
VALID_PUB = datetime(2026, 5, 17, 0, 0, 0, tzinfo=UTC)


def _make_sanitisation(**overrides: Any) -> SanitizationApplied:
    base: dict[str, Any] = {
        "html_stripped": True,
        "control_chars_removed": 0,
        "injection_markers_flagged": 0,
        "unicode_normalized_nfkc": True,
        "max_consecutive_whitespace_collapsed": False,
    }
    base.update(overrides)
    return SanitizationApplied(**base)


def _make_entry(**overrides: Any) -> RagProvenanceEntry:
    payload = b"# GEPA: Reflective Prompt Evolution\nbody body body\n"
    base: dict[str, Any] = {
        "doc_id": "ARXIV-2507.19457",
        "source": "arxiv",
        "source_url": "https://arxiv.org/abs/2507.19457",
        "source_domain": "arxiv.org",
        "title": "GEPA: Reflective Prompt Evolution",
        "authors": ("Anil Patil", "Omar Khattab"),
        "published_at": VALID_PUB,
        "ingested_at": VALID_NOW,
        "content_sha256": compute_content_sha256(payload),
        "content_length_chars": len(payload.decode("utf-8")),
        "whitelist_rule_version": "v1.0",
        "license": "arXiv non-exclusive distribution",
        "external_id": "arxiv:2507.19457",
        "category": ("cs.LG", "cs.AI"),
        "language_detected": "en",
        "sanitization_applied": _make_sanitisation(),
        "ingester_version": "frontier_crawler@1.0.0",
        "rejection_reason": None,
    }
    base.update(overrides)
    return RagProvenanceEntry(**base)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_whitelist_sources_locked_five() -> None:
    assert WHITELIST_SOURCES == frozenset(
        {"arxiv", "semanticscholar", "openreview", "github_releases", "akshare"}
    )


def test_doc_id_regex_accepts_all_five_prefixes() -> None:
    for ok in (
        "ARXIV-2507.19457",
        "S2-corpus123",
        "OPENREVIEW-Forum_ID-abc",
        "GH-REL-microsoft_RD-Agent-v0.5.0",
        "AKSHARE-2026-05-18-changelog",
    ):
        assert DOC_ID_RE.fullmatch(ok) is not None, ok


def test_doc_id_regex_rejects_unknown_prefix() -> None:
    for bad in ("WIKI-foo", "ARXIV", "ARXIV-", "arxiv-2507", "-prefix"):
        assert DOC_ID_RE.fullmatch(bad) is None, bad


def test_whitelist_rule_version_regex_accepts_v_dot_d() -> None:
    assert WHITELIST_RULE_VERSION_RE.fullmatch("v1.0") is not None
    assert WHITELIST_RULE_VERSION_RE.fullmatch("v23.456") is not None


def test_whitelist_rule_version_regex_rejects_bare_v1() -> None:
    assert WHITELIST_RULE_VERSION_RE.fullmatch("v1") is None
    assert WHITELIST_RULE_VERSION_RE.fullmatch("1.0") is None
    assert WHITELIST_RULE_VERSION_RE.fullmatch("v1.0-rc1") is None


# ---------------------------------------------------------------------------
# SanitizationApplied
# ---------------------------------------------------------------------------


def test_sanitisation_happy_path() -> None:
    s = _make_sanitisation()
    assert s.html_stripped is True
    assert s.control_chars_removed == 0


def test_sanitisation_is_frozen() -> None:
    s = _make_sanitisation()
    with pytest.raises(ValidationError):
        s.html_stripped = False  # type: ignore[misc]


def test_sanitisation_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        _make_sanitisation(control_chars_removed=-1)


def test_sanitisation_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        SanitizationApplied(
            html_stripped=True,
            control_chars_removed=0,
            injection_markers_flagged=0,
            unicode_normalized_nfkc=True,
            max_consecutive_whitespace_collapsed=False,
            extra_field=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# RagProvenanceEntry — happy paths
# ---------------------------------------------------------------------------


def test_entry_happy_path() -> None:
    entry = _make_entry()
    assert entry.source == "arxiv"
    assert entry.is_rejection is False
    assert entry.authors == ("Anil Patil", "Omar Khattab")
    # frozen + roundtrip
    serialized = entry.model_dump_json()
    again = RagProvenanceEntry.model_validate_json(serialized)
    assert again == entry


def test_entry_is_rejection_when_reason_set() -> None:
    entry = _make_entry(rejection_reason="failed Layer 2 datamarking")
    assert entry.is_rejection is True


def test_entry_is_frozen() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        entry.title = "edited"  # type: ignore[misc]


def test_entry_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        _make_entry(unexpected_field="oops")


# ---------------------------------------------------------------------------
# RagProvenanceEntry — validation
# ---------------------------------------------------------------------------


def test_entry_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        _make_entry(source="twitter")  # type: ignore[arg-type]


def test_entry_rejects_doc_id_prefix_source_mismatch() -> None:
    with pytest.raises(ValidationError, match="implies source"):
        _make_entry(doc_id="S2-foo", source="arxiv")


def test_entry_rejects_bad_content_sha() -> None:
    with pytest.raises(ValidationError):
        _make_entry(content_sha256="A" * 64)  # uppercase rejected


def test_entry_rejects_bad_whitelist_rule_version() -> None:
    with pytest.raises(ValidationError):
        _make_entry(whitelist_rule_version="v1")


def test_entry_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        _make_entry(source_url="not-a-url")


def test_entry_rejects_content_length_over_cap() -> None:
    with pytest.raises(ValidationError):
        _make_entry(content_length_chars=200_001)


def test_entry_rejects_more_than_fifty_authors() -> None:
    too_many = tuple(f"author-{i}" for i in range(51))
    with pytest.raises(ValidationError):
        _make_entry(authors=too_many)


def test_entry_rejects_unknown_language() -> None:
    with pytest.raises(ValidationError):
        _make_entry(language_detected="ja")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProvenanceWriter.write_entry — round-trip with the schema
# ---------------------------------------------------------------------------


def test_write_entry_round_trips_to_jsonl(tmp_path: Path) -> None:
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    entry = _make_entry()
    writer.write_entry(entry)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    again = RagProvenanceEntry.model_validate_json(lines[0])
    assert again == entry


def test_write_entry_rejects_non_pydantic_object(tmp_path: Path) -> None:
    from backend.evolution.provenance import ProvenanceAppendError

    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError, match="Pydantic"):
        writer.write_entry({"doc_id": "ARXIV-1"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProvenanceVerifier — lookup + hash-anchored citation
# ---------------------------------------------------------------------------


def _bootstrap_rag(tmp_path: Path) -> tuple[Path, RagProvenanceEntry, Path]:
    """Build a healthy data/rag/ + provenance.jsonl + payload markdown."""
    rag = tmp_path / "data" / "rag"
    for source in WHITELIST_SOURCES:
        (rag / source).mkdir(parents=True, exist_ok=True)
    provenance = rag / "provenance.jsonl"
    provenance.touch()
    payload_bytes = "# arxiv 2507 — GEPA\n\nReflective prompt evolution.\n".encode()
    entry = _make_entry(
        content_sha256=compute_content_sha256(payload_bytes),
        content_length_chars=len(payload_bytes.decode("utf-8")),
    )
    payload_dir = rag / entry.source / entry.ingested_at.date().isoformat()
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payload_dir / f"{entry.doc_id}.md"
    payload_path.write_bytes(payload_bytes)
    ProvenanceWriter(provenance).write_entry(entry)
    return rag, entry, payload_path


def test_verifier_lookup_returns_latest_entry(tmp_path: Path) -> None:
    rag, entry, _ = _bootstrap_rag(tmp_path)
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    again = verifier.lookup(entry.doc_id)
    assert again is not None
    assert again.doc_id == entry.doc_id


def test_verifier_lookup_returns_none_for_unknown_doc(tmp_path: Path) -> None:
    rag, _, _ = _bootstrap_rag(tmp_path)
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    assert verifier.lookup("ARXIV-9999.0001") is None


def test_verifier_lookup_returns_latest_when_duplicate_doc_id(
    tmp_path: Path,
) -> None:
    rag, entry, _ = _bootstrap_rag(tmp_path)
    # Append a rejection entry for the same doc_id (re-ingest after
    # a sanitisation failure scenario) and assert the verifier
    # surfaces the latest.
    superseded = _make_entry(
        rejection_reason="re-ingest rejected: injection markers > 0",
        ingester_version="frontier_crawler@1.0.1",
    )
    ProvenanceWriter(rag / "provenance.jsonl").write_entry(superseded)
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    again = verifier.lookup(entry.doc_id)
    assert again is not None
    assert again.is_rejection is True
    assert again.ingester_version == "frontier_crawler@1.0.1"


def test_verifier_verify_entry_happy_path(tmp_path: Path) -> None:
    rag, entry, payload_path = _bootstrap_rag(tmp_path)
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    resolved = verifier.verify_entry(entry)
    assert resolved == payload_path


def test_verifier_verify_entry_raises_on_missing_payload(tmp_path: Path) -> None:
    rag, entry, payload_path = _bootstrap_rag(tmp_path)
    payload_path.unlink()
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    with pytest.raises(ProvenanceVerifierError, match="missing"):
        verifier.verify_entry(entry)


def test_verifier_verify_entry_raises_on_tampered_payload(tmp_path: Path) -> None:
    rag, entry, payload_path = _bootstrap_rag(tmp_path)
    payload_path.write_bytes(b"# tampered\n")
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    with pytest.raises(ProvenanceVerifierError, match="hash-anchored citation"):
        verifier.verify_entry(entry)


def test_verifier_lookup_raises_on_corrupt_ledger_line(tmp_path: Path) -> None:
    rag, _, _ = _bootstrap_rag(tmp_path)
    with (rag / "provenance.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
    verifier = ProvenanceVerifier(
        rag_root=rag, provenance_path=rag / "provenance.jsonl"
    )
    with pytest.raises(ProvenanceVerifierError, match="corrupt ledger"):
        verifier.lookup("ARXIV-2507.19457")


def test_verifier_lookup_raises_on_missing_ledger(tmp_path: Path) -> None:
    verifier = ProvenanceVerifier(
        rag_root=tmp_path / "no-data" / "rag",
        provenance_path=tmp_path / "no-data" / "rag" / "provenance.jsonl",
    )
    with pytest.raises(ProvenanceVerifierError, match="missing"):
        verifier.lookup("ARXIV-1")


# ---------------------------------------------------------------------------
# Hash function — single source of truth
# ---------------------------------------------------------------------------


def test_compute_content_sha256_matches_known_value() -> None:
    assert compute_content_sha256(b"hello\n") == (
        "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


# ---------------------------------------------------------------------------
# Import-gate red line — models.py and verifier.py keep imports clean
# ---------------------------------------------------------------------------


def test_models_module_has_no_forbidden_backend_imports() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "backend/evolution/provenance/models.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from backend.api",
        "from backend.broker",
        "from backend.risk",
        "from backend.llm",
        "from backend.agents",
        "from backend.mirofish",
        "from backend.data",
    ):
        assert forbidden not in src, (
            f"models.py contains forbidden import {forbidden!r}"
        )


def test_verifier_module_has_no_forbidden_backend_imports() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "backend/evolution/provenance/verifier.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from backend.api",
        "from backend.broker",
        "from backend.risk",
        "from backend.llm",
        "from backend.agents",
        "from backend.mirofish",
        "from backend.data",
    ):
        assert forbidden not in src, (
            f"verifier.py contains forbidden import {forbidden!r}"
        )


# ---------------------------------------------------------------------------
# Repository-state smoke (provenance.jsonl exists at repo root)
# ---------------------------------------------------------------------------


def test_repo_provenance_jsonl_exists_and_is_empty_or_valid_json() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ledger = repo_root / "data" / "rag" / "provenance.jsonl"
    assert ledger.is_file()
    text = ledger.read_text(encoding="utf-8")
    if not text:
        return
    for line in text.splitlines():
        if line.strip():
            # Must at least be parseable JSON; schema check would
            # require every historical entry to satisfy the current
            # model, which is too tight for a long-lived ledger.
            json.loads(line)
