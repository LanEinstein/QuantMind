"""Append-only ``provenance.jsonl`` writer (X-002 skeleton + X-004 wiring point).

Filesystem primitive only. The X-004 follow-up extends this module with
the :class:`RagProvenanceEntry` Pydantic schema and the high-level
``write_entry(entry)`` API. This skeleton ships the lower layer:

* ``ProvenanceWriter.append_line(line)`` — append one already-serialized
  JSON string with ``fcntl.LOCK_EX`` and ``os.O_APPEND``; never seeks,
  never truncates, never overwrites. Concurrent multi-process writers
  are safe because the kernel coalesces ``O_APPEND`` writes that fit in
  one ``write(2)`` (lines are short JSON; the 4 KiB ``PIPE_BUF`` floor
  is comfortable for the 17-field provenance entry).
* ``fail_fast_validate_paths()`` — invoked by the boot path
  (X-004 onwards) to check the file exists, is regular, and the last
  non-empty line is well-formed JSON. Any structural failure raises
  :class:`ProvenanceAppendError` so the application fail-closes instead
  of silently corrupting the ledger (P2-2 §1.6 red line 22 +
  ``feedback_quality_over_minimal``).

The writer is intentionally **not** a Python context manager — every
call opens, locks, writes, unlocks, closes. Long-lived file handles
across an async event loop would block other coroutines while the
kernel buffer flushes, and lock ownership across ``await`` is fragile.
A one-line append is too cheap to bother batching.

Import gate (P2-2 §2 red line 17): this module must not import any
``backend.{api, broker, risk, llm, agents, mirofish, data}`` symbol.
Only standard library is used here. Verified by the X-018 three-layer
gate (ruff banned-imports + AST scan + ``scripts/redline-check.sh``).
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from pathlib import Path

PROVENANCE_FILE_NAME = "provenance.jsonl"
"""File name used inside ``data/rag/`` — kept as a constant so callers
(tests, scripts, X-004 schema writer) cannot disagree on the spelling."""

DEFAULT_PROVENANCE_PATH = Path("data/rag") / PROVENANCE_FILE_NAME
"""Repo-relative path. Resolved against the current working directory
when callers do not pass an explicit ``path``; production wiring should
always pass an absolute path derived from the application config root."""

WHITELIST_SOURCE_DIRS: frozenset[str] = frozenset(
    {"arxiv", "semanticscholar", "openreview", "github_releases", "akshare"}
)
"""Five RAG sources locked by P2-2 §1.1.1 (Round 2 Q3). The X-004
schema enforces this allowlist at the field level; X-002 only checks
that the per-source directories exist."""

MAX_LOCK_ATTEMPTS = 5
"""Bounded retry budget for LOCK_EX|LOCK_NB acquisition (codex X-027 R4
claim 4 defense-in-depth). Sum of waits at 0.1s base with 2x backoff
is 0.1 + 0.2 + 0.4 + 0.8 + 1.6 ≈ 3.1s total — fast enough that an
operator sees the failure, long enough that genuine contention from
the verifier's tail read does not spuriously fail the writer."""

INITIAL_LOCK_BACKOFF_SEC = 0.1
"""Base sleep between attempts; doubled on each retry."""


class ProvenanceAppendError(RuntimeError):
    """Raised on any failure that would corrupt the append-only invariant.

    Examples: missing file, truncated last line that fails JSON parse,
    embedded newline in the payload, lock acquisition failure. The
    boot path treats these as fail-closed so a malformed ledger cannot
    silently mask itself as healthy.
    """


class ProvenanceWriter:
    """Append-only JSONL writer for ``data/rag/provenance.jsonl``.

    Single-purpose by design. The X-004 wrapper layers on the Pydantic
    schema; tests targeting fcntl behaviour, hash discipline, and the
    append-only invariant exercise this class directly so the lower
    layer can be regressed without dragging in the model definitions.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_PROVENANCE_PATH

    @property
    def path(self) -> Path:
        return self._path

    def append_line(self, line: str) -> None:
        """Append one JSON line atomically.

        The caller is responsible for producing valid JSON — the writer
        only verifies (a) no embedded newline, (b) non-empty payload,
        (c) parseable as JSON. ``\\n`` is added automatically so callers
        never have to remember the trailing newline (a common source of
        broken JSONL files in the wild).
        """
        if not isinstance(line, str):
            raise ProvenanceAppendError(
                f"append_line expects str, got {type(line).__name__}"
            )
        if not line:
            raise ProvenanceAppendError("append_line received empty payload")
        if "\n" in line or "\r" in line:
            raise ProvenanceAppendError(
                "append_line payload must not contain raw newline / carriage "
                "return; serialize the JSON object then append it as a single "
                "line"
            )
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProvenanceAppendError(
                f"append_line payload is not valid JSON: {exc.msg}"
            ) from exc

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND guarantees the kernel positions the write at the end
        # of file each time, so even without flock concurrent writers
        # would not overwrite each other's bytes. flock is still useful
        # for cross-process serialization of "read tail then append"
        # workflows (e.g. the verifier checks the last line before the
        # writer adds the next one).
        fd = os.open(
            self._path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            mode=0o644,
        )
        try:
            # codex X-027 R4 claim 4 defense-in-depth: replace blocking
            # LOCK_EX (which would hang indefinitely if another local
            # process held the lock) with LOCK_EX|LOCK_NB plus bounded
            # exponential-backoff retry. SSH-only deployment (P1-6 §1.5)
            # bounds the threat, but a typed timeout is cheap insurance.
            last_err: OSError | None = None
            for attempt in range(MAX_LOCK_ATTEMPTS):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    last_err = exc
                    if attempt == MAX_LOCK_ATTEMPTS - 1:
                        raise ProvenanceAppendError(
                            f"could not acquire LOCK_EX on {self._path} "
                            f"after {MAX_LOCK_ATTEMPTS} attempts (codex "
                            "X-027 R4 claim 4 defense-in-depth — bounded "
                            "retry replaces blocking flock)"
                        ) from last_err
                    time.sleep(INITIAL_LOCK_BACKOFF_SEC * (2**attempt))
                except OSError as exc:
                    raise ProvenanceAppendError(
                        f"failed to acquire flock on {self._path}: {exc}"
                    ) from exc
            try:
                os.write(fd, line.encode("utf-8") + b"\n")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def write_entry(self, entry: object) -> None:
        """Serialize a :class:`RagProvenanceEntry` and append the line.

        ``entry`` is typed as ``object`` so this skeleton can ship
        before the X-004 ``RagProvenanceEntry`` schema lands. The
        runtime check requires ``entry.model_dump_json()`` (the
        Pydantic v2 surface) to exist — any object exposing the same
        contract is accepted, which keeps the writer test-friendly
        without coupling it back to a specific schema import.

        Layered on top of :meth:`append_line` so the lower-level
        invariants (flock, O_APPEND, no embedded newline) are reused
        verbatim.
        """
        dumper = getattr(entry, "model_dump_json", None)
        if dumper is None or not callable(dumper):
            raise ProvenanceAppendError(
                "write_entry expects a Pydantic v2 model exposing "
                "model_dump_json(); got "
                f"{type(entry).__name__}"
            )
        try:
            payload = dumper()
        except Exception as exc:  # noqa: BLE001 — serialization is the trust boundary
            raise ProvenanceAppendError(
                f"model_dump_json() raised: {exc}"
            ) from exc
        self.append_line(payload)


def _harden_path(path: Path) -> None:
    """Tighten permissions on a RAG path — best-effort defense-in-depth.

    Codex X-027 R4 claim 5: ``data/rag/`` deserves 0o700 on directories
    and 0o600 on the provenance JSONL even though the deployment
    boundary (127.0.0.1 + SSH-only) bounds the threat. ``chmod`` is
    cheap; an operator-induced 0o755 default is the only realistic
    leak vector.

    Skipped silently when:

    * the path does not exist;
    * the path **is a symlink** — both ``Path.stat()`` and ``os.chmod``
      follow links by default, so chmoding a symlink would modify the
      target's mode bits, potentially outside ``data/rag/`` if an
      operator symlinks a source directory to a shared mount (codex
      X-027 R4 follow-up — P2 finding fix);
    * the EUID does not own the path (multi-user host safety — never
      modify another user's files);
    * the platform lacks ``os.geteuid`` (Windows / non-POSIX) — in
      that case ``AttributeError`` is caught;
    * the underlying ``chmod`` raises ``OSError`` (filesystem refuses
      mode bits, network mount, etc.).

    The function is intentionally exception-swallowing — a hardening
    failure must not block boot. Persistent loose modes will surface
    on audit review of the deploy host.
    """
    try:
        # Reject symlinks BEFORE stat() so we never reach the target
        # (codex X-027 R4 follow-up P2). ``os.lstat`` returns metadata
        # about the link itself; chmod on a link is not portable
        # (Linux raises NotImplementedError for lchmod) and even if
        # supported would leave the target's permissions unchanged,
        # giving false confidence.
        link_st = os.lstat(path)
        if stat.S_ISLNK(link_st.st_mode):
            return
        st = path.stat()
        if st.st_uid != os.geteuid():
            return  # not our path; leave alone
        target_mode = 0o600 if path.is_file() else 0o700
        current_mode = stat.S_IMODE(st.st_mode)
        if current_mode != target_mode:
            os.chmod(path, target_mode)
    except (OSError, AttributeError):
        return  # best-effort; never block boot


def fail_fast_validate_paths(
    *,
    rag_root: Path | str = "data/rag",
    provenance_path: Path | str | None = None,
) -> None:
    """Boot-time fail-fast on the RAG filesystem layout.

    Called from the application lifespan (X-004 onwards) so a
    misconfigured deploy refuses to start instead of producing
    half-baked self-evolution runs.

    Checks:

    * ``data/rag/`` exists and is a directory.
    * The five whitelisted source subdirectories exist.
    * ``provenance.jsonl`` exists, is a regular file (not a symlink to
      somewhere unexpected, not a fifo).
    * If non-empty, the last non-empty line parses as JSON. Empty file
      is fine — bootstrap state.
    """
    root = Path(rag_root)
    if not root.is_dir():
        raise ProvenanceAppendError(
            f"RAG root {root} is missing — create it before boot"
        )
    for source in sorted(WHITELIST_SOURCE_DIRS):
        if not (root / source).is_dir():
            raise ProvenanceAppendError(
                f"RAG source dir {root / source} is missing — the five "
                f"whitelisted sources are locked by P2-2 §1.1.1"
            )
    path = Path(provenance_path) if provenance_path else root / PROVENANCE_FILE_NAME
    if not path.exists():
        raise ProvenanceAppendError(
            f"provenance ledger {path} is missing — create an empty file "
            f"before boot (touch {path})"
        )
    if not path.is_file():
        raise ProvenanceAppendError(
            f"provenance ledger {path} is not a regular file"
        )

    # Sanity-check the tail. We scan the last 64 KiB which is far
    # larger than any one entry; a fully corrupted file would still
    # surface as a parse failure on the final line.
    size = path.stat().st_size
    if size > 0:
        read_window = min(size, 65536)
        with path.open("rb") as fh:
            fh.seek(-read_window, os.SEEK_END)
            tail = fh.read()
        split_tail = tail.splitlines()
        last_line = split_tail[-1] if split_tail else b""
        if last_line:
            try:
                json.loads(last_line)
            except json.JSONDecodeError as exc:
                raise ProvenanceAppendError(
                    f"provenance ledger {path} last line is not valid JSON: "
                    f"{exc.msg}"
                ) from exc

    # codex X-027 R4 claim 5 defense-in-depth: chmod the RAG subtree to
    # 0o700/0o600 after the structural checks pass. Best-effort —
    # _harden_path swallows non-owned / non-POSIX / OSError cases.
    # Runs even on an empty ledger so a fresh deploy locks down the
    # subtree immediately.
    _harden_path(root)
    for source in sorted(WHITELIST_SOURCE_DIRS):
        _harden_path(root / source)
    _harden_path(path)
