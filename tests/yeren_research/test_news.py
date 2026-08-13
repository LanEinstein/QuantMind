from __future__ import annotations

from datetime import datetime
from typing import Any

from scripts.yeren_research.news import audit_news_collection


class _Collection:
    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assert "$facet" in pipeline[0]
        return [
            {
                "overall": [
                    {
                        "row_count": 3,
                        "publish_time_start": datetime(2026, 5, 1),
                        "publish_time_end": datetime(2026, 5, 2),
                    }
                ],
                "sources": [
                    {
                        "_id": "source-a",
                        "row_count": 3,
                        "publish_time_start": datetime(2026, 5, 1),
                        "publish_time_end": datetime(2026, 5, 2),
                    }
                ],
            }
        ]


def test_news_audit_reports_ranges_without_inventing_timezone() -> None:
    result = audit_news_collection(_Collection())

    assert result["row_count"] == 3
    assert result["publish_time_start"] == "2026-05-01T00:00:00"
    assert result["sources"][0]["source"] == "source-a"
    assert "timezone" in result["time_caveat"]
