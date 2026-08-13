"""Read-only coverage audit for the local latest-news collection."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def audit_news_collection(collection: Any) -> dict[str, Any]:
    """Describe stored history without assuming that source clocks are aligned."""
    result: dict[str, Any] = next(
        iter(
            collection.aggregate(
                [
                    {
                        "$facet": {
                            "overall": [
                                {
                                    "$group": {
                                        "_id": None,
                                        "row_count": {"$sum": 1},
                                        "publish_time_start": {"$min": "$publish_time"},
                                        "publish_time_end": {"$max": "$publish_time"},
                                    }
                                }
                            ],
                            "sources": [
                                {
                                    "$group": {
                                        "_id": "$source",
                                        "row_count": {"$sum": 1},
                                        "publish_time_start": {"$min": "$publish_time"},
                                        "publish_time_end": {"$max": "$publish_time"},
                                    }
                                },
                                {"$sort": {"_id": 1}},
                            ],
                        }
                    }
                ]
            )
        ),
        {"overall": [], "sources": []},
    )
    overall = result.get("overall") or []
    totals = overall[0] if overall else {}
    return {
        "row_count": int(totals.get("row_count") or 0),
        "publish_time_start": _iso(totals.get("publish_time_start")),
        "publish_time_end": _iso(totals.get("publish_time_end")),
        "sources": [
            {
                "source": str(row.get("_id") or "unknown"),
                "row_count": int(row.get("row_count") or 0),
                "publish_time_start": _iso(row.get("publish_time_start")),
                "publish_time_end": _iso(row.get("publish_time_end")),
            }
            for row in result.get("sources") or []
        ],
        "time_caveat": (
            "PyMongo returns UTC datetimes without tzinfo by default; current crawler "
            "also assigns UTC to naive vendor values, so source timezone contracts "
            "must be verified before strict as-of use."
        ),
    }


def audit_local_news(*, uri: str, database: str, collection: str) -> dict[str, Any]:
    """Connect to the owner-scoped local Mongo instance for a read-only audit."""
    from pymongo import MongoClient

    client: Any = MongoClient(uri, serverSelectionTimeoutMS=3_000)
    try:
        client.admin.command("ping")
        return audit_news_collection(client[database][collection])
    finally:
        client.close()
