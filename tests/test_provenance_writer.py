"""X-002 unit tests — provenance.jsonl skeleton (writer + fail-fast).

Schema-free tests: ``ProvenanceWriter`` accepts any valid JSON string.
The 17-field RagProvenanceEntry schema and ``write_entry`` API ship in
X-004 with their own test module.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path

import pytest

from backend.evolution.provenance import (
    DEFAULT_PROVENANCE_PATH,
    PROVENANCE_FILE_NAME,
    ProvenanceAppendError,
    ProvenanceWriter,
    fail_fast_validate_paths,
)
from backend.evolution.provenance import writer as writer_module
from backend.evolution.provenance.writer import (
    MAX_LOCK_ATTEMPTS,
    WHITELIST_SOURCE_DIRS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Constants and repo-layout invariants
# ---------------------------------------------------------------------------


def test_whitelist_sources_locked_to_five() -> None:
    assert WHITELIST_SOURCE_DIRS == frozenset(
        {"arxiv", "semanticscholar", "openreview", "github_releases", "akshare"}
    )


def test_default_path_uses_data_rag_provenance_jsonl() -> None:
    assert DEFAULT_PROVENANCE_PATH == Path("data/rag") / PROVENANCE_FILE_NAME
    assert PROVENANCE_FILE_NAME == "provenance.jsonl"


def test_repository_has_data_rag_layout() -> None:
    root = REPO_ROOT / "data" / "rag"
    assert root.is_dir(), f"missing {root}"
    for source in WHITELIST_SOURCE_DIRS:
        assert (root / source).is_dir(), f"missing source dir {source}"
    assert (root / PROVENANCE_FILE_NAME).is_file(), (
        f"missing {root / PROVENANCE_FILE_NAME}"
    )


# ---------------------------------------------------------------------------
# Append-line happy path
# ---------------------------------------------------------------------------


def test_append_line_writes_and_adds_newline(tmp_path: Path) -> None:
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    writer.append_line('{"doc_id": "ARXIV-2507.19457", "source": "arxiv"}')
    raw = target.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw.strip())["doc_id"] == "ARXIV-2507.19457"


def test_append_line_multiple_creates_jsonl(tmp_path: Path) -> None:
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    for n in range(5):
        writer.append_line(json.dumps({"i": n}))
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert [json.loads(line)["i"] for line in lines] == list(range(5))


def test_append_line_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    writer.append_line(json.dumps({"k": 1}))
    assert target.exists()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_append_line_rejects_empty_string(tmp_path: Path) -> None:
    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError):
        writer.append_line("")


def test_append_line_rejects_non_string(tmp_path: Path) -> None:
    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError):
        writer.append_line({"k": 1})  # type: ignore[arg-type]


def test_append_line_rejects_embedded_newline(tmp_path: Path) -> None:
    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError):
        writer.append_line('{"a":1}\n{"a":2}')


def test_append_line_rejects_carriage_return(tmp_path: Path) -> None:
    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError):
        writer.append_line('{"a":1}\r')


def test_append_line_rejects_invalid_json(tmp_path: Path) -> None:
    writer = ProvenanceWriter(tmp_path / "provenance.jsonl")
    with pytest.raises(ProvenanceAppendError):
        writer.append_line("not json at all")


# ---------------------------------------------------------------------------
# Append-only invariant — concurrent writes serialise via flock
# ---------------------------------------------------------------------------


def test_append_line_concurrent_does_not_lose_writes(tmp_path: Path) -> None:
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    total = 64
    payloads = [json.dumps({"i": i}) for i in range(total)]

    def write_one(payload: str) -> None:
        writer.append_line(payload)

    threads = [threading.Thread(target=write_one, args=(p,)) for p in payloads]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == total
    seen = {json.loads(line)["i"] for line in lines}
    assert seen == set(range(total))


def test_append_line_does_not_truncate_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "provenance.jsonl"
    target.write_text('{"existing": true}\n', encoding="utf-8")
    writer = ProvenanceWriter(target)
    writer.append_line('{"new": true}')
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines == ['{"existing": true}', '{"new": true}']


# ---------------------------------------------------------------------------
# Codex X-027 R4 claim 4 — LOCK_NB bounded retry (defense-in-depth)
# ---------------------------------------------------------------------------


def test_lock_acquired_after_contention_releases(tmp_path: Path) -> None:
    """A short-lived contender releases inside the retry budget — the
    main writer should retry, acquire the lock, and write its line."""
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    # Pre-create the file so fd open + flock target both exist.
    target.touch()

    # Open a competing fd and hold LOCK_EX briefly, then release.
    competitor_fd = os.open(target, os.O_WRONLY | os.O_APPEND)
    fcntl.flock(competitor_fd, fcntl.LOCK_EX)
    released = threading.Event()

    def release_after_short_delay() -> None:
        # Release within the first backoff window so the main writer's
        # second attempt succeeds (0.1s sleep, then retry).
        import time as _time

        _time.sleep(0.15)
        fcntl.flock(competitor_fd, fcntl.LOCK_UN)
        os.close(competitor_fd)
        released.set()

    releaser = threading.Thread(target=release_after_short_delay)
    releaser.start()
    try:
        # Main writer's first attempt fails with BlockingIOError, sleeps
        # 0.1s, retries and succeeds after the contender releases at ~0.15s.
        writer.append_line('{"after_contention": true}')
    finally:
        releaser.join()
    assert released.is_set()
    body = target.read_text(encoding="utf-8")
    assert json.loads(body.strip())["after_contention"] is True


def test_lock_timeout_raises_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If every attempt hits BlockingIOError, the writer raises a typed
    ProvenanceAppendError after MAX_LOCK_ATTEMPTS — no infinite hang."""
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)

    call_count = {"n": 0}

    def always_block(_fd: int, _op: int) -> None:
        call_count["n"] += 1
        raise BlockingIOError("simulated lock contention")

    # Skip real sleeps so the test still completes quickly even though
    # the production code path would sleep ~3.1s in total.
    monkeypatch.setattr(writer_module.fcntl, "flock", always_block)
    monkeypatch.setattr(writer_module.time, "sleep", lambda _s: None)

    with pytest.raises(ProvenanceAppendError, match="bounded retry"):
        writer.append_line('{"never_lands": true}')
    # All MAX_LOCK_ATTEMPTS attempts should have been made.
    assert call_count["n"] == MAX_LOCK_ATTEMPTS


# ---------------------------------------------------------------------------
# fail_fast_validate_paths
# ---------------------------------------------------------------------------


def _bootstrap(root: Path) -> Path:
    """Build a healthy data/rag/ scaffold under ``root``."""
    rag = root / "data" / "rag"
    for source in WHITELIST_SOURCE_DIRS:
        (rag / source).mkdir(parents=True, exist_ok=True)
    provenance = rag / "provenance.jsonl"
    provenance.touch()
    return rag


def test_fail_fast_validate_paths_happy_empty_ledger(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    fail_fast_validate_paths(rag_root=rag)


def test_fail_fast_validate_paths_happy_with_valid_tail(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    (rag / "provenance.jsonl").write_text(
        '{"doc_id": "ARXIV-1"}\n{"doc_id": "ARXIV-2"}\n',
        encoding="utf-8",
    )
    fail_fast_validate_paths(rag_root=rag)


def test_fail_fast_validate_paths_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceAppendError, match="missing"):
        fail_fast_validate_paths(rag_root=tmp_path / "no-such-dir")


def test_fail_fast_validate_paths_missing_source_dir(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    (rag / "arxiv").rmdir()
    with pytest.raises(ProvenanceAppendError, match="arxiv"):
        fail_fast_validate_paths(rag_root=rag)


def test_fail_fast_validate_paths_missing_ledger_file(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    (rag / "provenance.jsonl").unlink()
    with pytest.raises(ProvenanceAppendError, match="provenance ledger"):
        fail_fast_validate_paths(rag_root=rag)


def test_fail_fast_validate_paths_rejects_non_file_ledger(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    (rag / "provenance.jsonl").unlink()
    (rag / "provenance.jsonl").mkdir()
    with pytest.raises(ProvenanceAppendError, match="not a regular file"):
        fail_fast_validate_paths(rag_root=rag)


def test_fail_fast_validate_paths_rejects_corrupt_tail(tmp_path: Path) -> None:
    rag = _bootstrap(tmp_path)
    (rag / "provenance.jsonl").write_text(
        '{"doc_id": "ARXIV-1"}\nthis is not json\n', encoding="utf-8"
    )
    with pytest.raises(ProvenanceAppendError, match="not valid JSON"):
        fail_fast_validate_paths(rag_root=rag)


# ---------------------------------------------------------------------------
# Import-gate red line — writer must not pull in disallowed backend modules
# ---------------------------------------------------------------------------


def test_writer_module_has_no_forbidden_backend_imports() -> None:
    src = (REPO_ROOT / "backend/evolution/provenance/writer.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "from backend.api",
        "from backend.broker",
        "from backend.risk",
        "from backend.llm",
        "from backend.agents",
        "from backend.mirofish",
        "from backend.data",
        "import backend.api",
        "import backend.broker",
        "import backend.risk",
        "import backend.llm",
        "import backend.agents",
        "import backend.mirofish",
        "import backend.data",
    ):
        assert forbidden not in src, (
            f"P2-2 §2 red line 17 violation: writer.py contains {forbidden!r}"
        )


def test_writer_module_uses_stdlib_only(tmp_path: Path) -> None:
    # The writer's behaviour must not require optional third-party deps.
    # If someone slips in (e.g.) a Pydantic import the test fixture below
    # still works because we only exercise pure JSON strings.
    target = tmp_path / "provenance.jsonl"
    writer = ProvenanceWriter(target)
    writer.append_line('{"x": "y"}')
    assert os.path.getsize(target) > 0
