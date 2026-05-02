#!/usr/bin/env python3
"""Phase 5B-T03 shadow comparison CLI.

Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
prints the action-consistency / confidence-deviation report Phase 5B
exit gates on. The actual math lives in
:mod:`backend.services.shadow_compare` so it stays unit-testable.

Usage::

    # MongoDB (default; reads MONGODB_URI from env)
    python scripts/shadow_compare.py --days 7

    # File replay (operator-collected JSONL of shadow_decisions docs)
    python scripts/shadow_compare.py --input shadow_dump.jsonl

The script returns exit code 0 when all gates pass, 1 otherwise. Useful
in CI as the Phase 5B exit gate driver.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Allow running as a standalone script without `pip install -e .`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.shadow_compare import (  # noqa: E402
    compute_shadow_report,
    render_markdown,
)

_MAX_DAYS = 30
_MAX_JSONL_LINES = 200_000  # ≫ realistic 30d × 30-stock × 4-cron ≈ 25k


def _bounded_days(value: str) -> int:
    """Clamp ``--days`` to ``[1, _MAX_DAYS]`` (codex P5B-exit R5 MED)."""
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--days must be an integer, got {value!r}"
        ) from exc
    if days < 1 or days > _MAX_DAYS:
        raise argparse.ArgumentTypeError(
            f"--days must be in [1, {_MAX_DAYS}], got {days}"
        )
    return days


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 5B shadow comparison report.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="JSONL file of shadow_decisions documents. Mutually "
        "exclusive with the MongoDB path.",
    )
    parser.add_argument(
        "--days",
        type=_bounded_days,
        default=7,
        help=f"Look-back window in days (max {_MAX_DAYS}).",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        help="MongoDB connection URI (default: $MONGODB_URI).",
    )
    parser.add_argument(
        "--mongo-db",
        default=os.environ.get("MONGODB_DB", "quantmind"),
        help="MongoDB database name.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on first failing gate (CI driver mode).",
    )
    return parser.parse_args(argv)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file. Lines that fail to parse are surfaced loudly.

    A silent skip would let a corrupted dump pass the gate by reporting
    metrics over a smaller-than-expected sample; instead we raise so the
    operator notices and re-dumps. The line cap is a DoS guard
    (codex P5B-exit R5 MED) — well above any realistic shadow-test
    volume but bounded so a hostile dump cannot exhaust memory.
    """
    docs: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            if lineno > _MAX_JSONL_LINES:
                raise SystemExit(
                    f"{path}: input exceeds {_MAX_JSONL_LINES} lines"
                )
            line = raw.strip()
            if not line:
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"{path}:{lineno}: malformed JSON ({exc})"
                ) from exc
    return docs


async def _read_mongo(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Connect to MongoDB and pull shadow_decisions for the given window.

    Imported lazily so tests / file-mode callers don't pay the motor
    import cost.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    from backend.data.database import MongoDBService
    from backend.services.shadow_recorder import query_shadow_decisions

    client = AsyncIOMotorClient(args.mongo_uri)
    try:
        db = client[args.mongo_db]
        service = MongoDBService(db)
        docs = await query_shadow_decisions(service, days=args.days)
        return list(docs)
    finally:
        client.close()


def _load_docs(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
    if args.input is not None:
        if not args.input.exists():
            raise SystemExit(f"input file does not exist: {args.input}")
        return _read_jsonl(args.input)
    return asyncio.run(_read_mongo(args))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    docs = list(_load_docs(args))
    report = compute_shadow_report(docs)
    sys.stdout.write(render_markdown(report))
    if args.strict and not all(report.passes.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
