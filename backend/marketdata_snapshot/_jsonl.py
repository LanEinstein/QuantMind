"""Tiny append-only JSONL helpers shared by the manifest stores (K-003).

Append-only: rows are only ever appended, never rewritten or deleted —
the same insert-only / no-mutation / no-delete discipline the snapshot
store enforces (P1-2.A red lines). Writes are serialised by a filelock
so a concurrent appender cannot interleave a partial line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from filelock import FileLock


class JsonlStoreError(RuntimeError):
    """Raised when a JSONL row is corrupt or an invariant fails."""


def append_row(path: Path, row: dict[str, Any], lock: FileLock) -> None:
    """Append one canonical JSON object as a line under ``lock``."""
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    row, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                + "\n"
            )


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read all rows (offline, no network). Missing file -> empty list."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise JsonlStoreError(
                f"corrupt JSONL row at {path}:{lineno}: {exc}"
            ) from exc
    return rows


__all__ = ["JsonlStoreError", "append_row", "load_rows"]
