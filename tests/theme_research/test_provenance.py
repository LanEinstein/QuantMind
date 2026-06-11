"""Provenance capture invariants (Y-002) — raw bytes, checksum, promotability."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.theme_research.provenance import (
    ThemeArtifactType,
    ThemeResearchRun,
    ThemeResearchSnapshot,
    ThemeResearchStore,
    theme_sha256,
)

_T = datetime(2026, 6, 11, 1, 0, tzinfo=UTC)


def _snap(payload: bytes, kind: ThemeArtifactType) -> ThemeResearchSnapshot:
    return ThemeResearchSnapshot.create(
        artifact_type=kind,
        raw_payload=payload,
        encoding="utf-8",
        compression="none",
        fetch_time_utc=_T,
    )


def test_snapshot_create_self_validates() -> None:
    snap = _snap(b"hello", ThemeArtifactType.PAGE)
    assert snap.size == 5
    assert snap.raw_payload_sha256 == theme_sha256(b"hello")


def test_tampered_size_rejected() -> None:
    with pytest.raises(ValueError, match="size"):
        ThemeResearchSnapshot(
            artifact_type=ThemeArtifactType.PAGE,
            raw_payload=b"hello",
            size=4,
            encoding="utf-8",
            compression="none",
            raw_payload_sha256=theme_sha256(b"hello"),
            fetch_time_utc=_T,
        )


def test_tampered_hash_rejected() -> None:
    with pytest.raises(ValueError, match="sha256 mismatch"):
        ThemeResearchSnapshot(
            artifact_type=ThemeArtifactType.PAGE,
            raw_payload=b"hello",
            size=5,
            encoding="utf-8",
            compression="none",
            raw_payload_sha256="b" * 64,
            fetch_time_utc=_T,
        )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ThemeResearchSnapshot.create(
            artifact_type=ThemeArtifactType.PAGE,
            raw_payload=b"x",
            encoding="utf-8",
            compression="none",
            fetch_time_utc=datetime(2026, 6, 11, 1, 0),  # naive
        )


def test_store_roundtrip_content_addressed(tmp_path) -> None:
    store = ThemeResearchStore(tmp_path)
    snap = _snap(b"page-bytes", ThemeArtifactType.PAGE)
    store.put_snapshot(snap)
    assert store.get_payload(snap.raw_payload_sha256) == b"page-bytes"
    # Idempotent: same bytes stored twice is fine (content-addressed).
    store.put_snapshot(_snap(b"page-bytes", ThemeArtifactType.PAGE))
    assert store.get_payload(snap.raw_payload_sha256) == b"page-bytes"


def test_store_missing_payload_returns_none(tmp_path) -> None:
    store = ThemeResearchStore(tmp_path)
    assert store.get_payload("c" * 64) is None


def test_store_duplicate_snapshot_id_rejected(tmp_path) -> None:
    """Append-only same-id rejection: re-putting an id is refused."""
    store = ThemeResearchStore(tmp_path)
    snap = _snap(b"page-a", ThemeArtifactType.PAGE)
    store.put_snapshot(snap)
    # Same id, DIFFERENT bytes -> would make audit/replay ambiguous.
    dup = ThemeResearchSnapshot.create(
        artifact_type=ThemeArtifactType.PAGE,
        raw_payload=b"page-b-different",
        encoding="utf-8",
        compression="none",
        fetch_time_utc=_T,
    ).model_copy(update={"snapshot_id": snap.snapshot_id})
    with pytest.raises(ValueError, match="append-only"):
        store.put_snapshot(dup)


def test_store_corrupt_payload_fails_closed(tmp_path) -> None:
    store = ThemeResearchStore(tmp_path)
    snap = _snap(b"clean", ThemeArtifactType.PAGE)
    store.put_snapshot(snap)
    sha = snap.raw_payload_sha256
    blob = tmp_path / "payloads" / sha[:2] / f"{sha}.bin"
    blob.write_bytes(b"TAMPERED")
    with pytest.raises(ValueError, match="checksum"):
        store.get_payload(snap.raw_payload_sha256)


def _run(
    *,
    types: tuple[ThemeArtifactType, ...],
    captured_pages: tuple[tuple[str, str], ...] = (),
    output_sha: str = "d" * 64,
    cited_pages: tuple[tuple[str, str], ...] = (),
) -> ThemeResearchRun:
    return ThemeResearchRun(
        run_id="run-1",
        started_at=_T,
        prompt_version_hash="e" * 64,
        snapshot_ids=(),
        captured_types=types,
        captured_pages=captured_pages,
        output_sha256=output_sha,
        cited_pages=cited_pages,
    )


def test_run_promotable_when_all_bytes_present() -> None:
    page = ("www.gov.cn", "f" * 64)
    run = _run(
        types=(
            ThemeArtifactType.PROMPT,
            ThemeArtifactType.LLM_RESPONSE,
            ThemeArtifactType.PAGE,
        ),
        captured_pages=(page,),
        cited_pages=(page,),
    )
    ok, reason = run.is_promotable()
    assert ok, reason


def test_run_non_promotable_without_prompt() -> None:
    run = _run(types=(ThemeArtifactType.LLM_RESPONSE,))
    ok, reason = run.is_promotable()
    assert not ok and "PROMPT" in reason


def test_run_non_promotable_without_response() -> None:
    run = _run(types=(ThemeArtifactType.PROMPT,))
    ok, reason = run.is_promotable()
    assert not ok and "LLM_RESPONSE" in reason


def test_run_non_promotable_without_output_digest() -> None:
    run = _run(
        types=(ThemeArtifactType.PROMPT, ThemeArtifactType.LLM_RESPONSE),
        output_sha="",
    )
    ok, reason = run.is_promotable()
    assert not ok and "output" in reason


def test_run_non_promotable_when_cited_snippet_not_captured() -> None:
    """Adversarial: the output cites a snippet the run never byte-captured."""
    run = _run(
        types=(ThemeArtifactType.PROMPT, ThemeArtifactType.LLM_RESPONSE),
        captured_pages=(("www.gov.cn", "1" * 64),),
        cited_pages=(("www.gov.cn", "9" * 64),),  # sha never captured
    )
    ok, reason = run.is_promotable()
    assert not ok and "not byte-captured" in reason


def test_run_non_promotable_when_citation_domain_mismatches() -> None:
    """Adversarial: cited sha was captured, but the claimed domain is wrong."""
    run = _run(
        types=(ThemeArtifactType.PROMPT, ThemeArtifactType.LLM_RESPONSE),
        captured_pages=(("www.gov.cn", "1" * 64),),
        cited_pages=(("www.evil.com", "1" * 64),),  # right sha, wrong domain
    )
    ok, reason = run.is_promotable()
    assert not ok and "does not match" in reason


def test_run_rejects_malformed_output_sha() -> None:
    with pytest.raises(ValueError, match="64-char"):
        _run(
            types=(ThemeArtifactType.PROMPT, ThemeArtifactType.LLM_RESPONSE),
            output_sha="not-a-real-digest",
        )


def test_store_run_duplicate_rejected(tmp_path) -> None:
    store = ThemeResearchStore(tmp_path)
    run = _run(types=(ThemeArtifactType.PROMPT, ThemeArtifactType.LLM_RESPONSE))
    store.put_run(run)
    with pytest.raises(ValueError, match="append-only"):
        store.put_run(run)
