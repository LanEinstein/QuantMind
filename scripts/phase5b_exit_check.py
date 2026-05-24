#!/usr/bin/env python3
"""Phase 5B exit-gate verification CLI.

Aggregates the SSoT §6.972 exit checklist into one markdown report:

* fast / slow per-stock cost p95 (from Redis cost_tracker)
* fast / slow p95 latency (from MongoDB analysis_records)
* daily total cost (from Redis cost_tracker)
* shadow consistency (from MongoDB shadow_decisions)

The math lives in :mod:`backend.services.phase5b_exit_check`; this
module only handles I/O wiring and ``argparse``.

Usage::

    python scripts/phase5b_exit_check.py --days 7

Returns exit 0 when every gate passes, 1 otherwise (intended for CI).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Allow running as a standalone script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.phase5b_exit_check import (  # noqa: E402
    compute_exit_report,
    render_markdown,
)
from backend.services.universe_policy import load_policy  # noqa: E402

_MAX_DAYS = 30  # cap matches shadow_decisions TTL retention


def _bounded_days(value: str) -> int:
    """argparse type for ``--days`` — clamp to ``[1, _MAX_DAYS]``.

    Codex P5B-exit R5 MED: an unbounded look-back can OOM the runner /
    overload Mongo, and a value below the SSoT 7-day shadow window is
    not a meaningful gate. Both ends are validated up front.
    """
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
        description="Phase 5B exit-gate verification.",
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
    )
    parser.add_argument(
        "--mongo-db",
        default=os.environ.get("MONGODB_DB", "quantmind"),
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    )
    parser.add_argument(
        "--policy-path",
        type=Path,
        default=Path("config/universe_policy.yaml"),
        help="Path to universe_policy.yaml (relative to project root).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on first failing gate.",
    )
    return parser.parse_args(argv)


async def _gather_inputs(
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Pull records / cost entries / shadow docs from live infra.

    We open the Mongo client and Redis client locally and close them
    before returning so the caller (sync ``main``) does not have to know
    about async lifetimes.
    """
    import datetime as _dt

    from motor.motor_asyncio import AsyncIOMotorClient
    from redis.asyncio import Redis

    from backend.data.database import MongoDBService
    from backend.llm.cost_tracker import aggregate_costs
    from backend.services.shadow_recorder import query_shadow_decisions

    client = AsyncIOMotorClient(args.mongo_uri)
    redis: Redis | None = None
    try:
        db = client[args.mongo_db]
        service = MongoDBService(db)

        # Filter analysis records by the requested window — falling back
        # to ``limit(days * 200)`` (codex P5B-exit R1 P2) lets a noisy
        # date range mask in-window runs and pulls in older latencies
        # that should not weight the report. The ``$or`` matches both
        # raw BSON Date (older code paths) and ISO strings (current
        # ``model_dump(mode="json")`` writers).
        cutoff = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=args.days)
        cutoff_iso = cutoff.isoformat()
        # Project only the five scalar fields the gate actually needs.
        # Full ``analysis_records`` documents carry agent step content,
        # debate transcripts, and evidence payloads (50-200KB each);
        # at a 30-day window with 30-stock fast cron that's hundreds
        # of MB transferred for nothing (codex P5B-exit R3 P2).
        projection = {
            "_id": 0,
            "run_id": 1,
            "stock_code": 1,
            "trade_date": 1,
            "created_at": 1,
            "completed_at": 1,
        }
        records_cursor = (
            service._db["analysis_records"]  # noqa: SLF001
            .find(
                {
                    "$or": [
                        {"created_at": {"$gte": cutoff}},
                        {"created_at": {"$gte": cutoff_iso}},
                    ]
                },
                projection,
            )
            .sort("created_at", -1)
        )
        records = [doc async for doc in records_cursor]

        shadow_docs = await query_shadow_decisions(service, days=args.days)

        redis = Redis.from_url(args.redis_url, decode_responses=True)
        summary = await aggregate_costs(redis, days=args.days)
        cost_entries = [
            {
                "date": entry.date,
                "agent_name": entry.agent_name,
                "cost_rmb": entry.cost_rmb,
            }
            for entry in summary.entries
        ]
        return list(records), cost_entries, list(shadow_docs)
    finally:
        if redis is not None:
            await redis.aclose()
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.policy_path.exists():
        sys.stderr.write(
            f"universe_policy.yaml not found at {args.policy_path}\n"
        )
        return 2
    policy = load_policy(args.policy_path)

    records, cost_entries, shadow_docs = asyncio.run(_gather_inputs(args))
    report = compute_exit_report(
        records,
        cost_entries,
        shadow_docs,
        policy,
        days=args.days,
    )
    sys.stdout.write(render_markdown(report))
    if args.strict and not all(report.passes.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
