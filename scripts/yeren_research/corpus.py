"""Deterministic candidate discovery over original transcript text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.yeren_research.inventory import read_jsonl

TERM_GROUPS: dict[str, tuple[str, ...]] = {
    "market": ("大盘", "指数", "量能", "成交量", "情绪", "板块", "主线"),
    "position": ("空仓", "试错", "加仓", "减仓", "锁仓", "推仓", "仓位"),
    "exit": ("止损", "止盈", "卖出", "清仓", "兑现", "离场"),
    "news": ("消息", "政策", "公告", "传闻", "利好", "利空"),
    "earnings": ("财报", "业绩", "季报", "年报", "利润", "营收"),
    "selection": ("选股", "龙头", "补涨", "低位", "趋势", "估值", "成长"),
}


def find_candidates(
    corpus_root: Path,
    *,
    required_groups: frozenset[str] = frozenset(),
    year: int | None = None,
) -> list[dict[str, Any]]:
    """Return chronological keyword candidates for GPT review, without judging them."""
    unknown = required_groups - TERM_GROUPS.keys()
    if unknown:
        raise ValueError(f"unknown term groups: {', '.join(sorted(unknown))}")
    metadata = {
        str(row["aweme_id"]): row for row in read_jsonl(corpus_root / "metadata.jsonl")
    }
    candidates: list[dict[str, Any]] = []
    for path in sorted((corpus_root / "transcripts").glob("*.json")):
        video = metadata.get(path.stem)
        if video is None:
            continue
        published_at = str(video.get("published_at") or "")
        if year is not None and not published_at.startswith(str(year)):
            continue
        transcript = json.loads(path.read_text(encoding="utf-8"))
        text = str(transcript.get("text") or "")
        matched = {
            group: [term for term in terms if term in text]
            for group, terms in TERM_GROUPS.items()
        }
        matched = {group: terms for group, terms in matched.items() if terms}
        if not required_groups.issubset(matched):
            continue
        candidates.append(
            {
                "aweme_id": path.stem,
                "published_at": published_at,
                "title": str(video.get("description") or video.get("title") or ""),
                "matched_terms": matched,
            }
        )
    return sorted(candidates, key=lambda row: (row["published_at"], row["aweme_id"]))
