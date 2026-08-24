from __future__ import annotations

import json
from pathlib import Path

from scripts.yeren_research.corpus import find_candidates


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_candidates_use_corpus_description_and_filter_groups(tmp_path: Path) -> None:
    (tmp_path / "metadata.jsonl").write_text(
        json.dumps(
            {
                "aweme_id": "v1",
                "published_at": "2025-08-04T18:15:27+08:00",
                "description": "收盘复盘",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "transcripts" / "v1.json",
        {"text": "大盘出现机会，模式内推仓位。"},
    )

    candidates = find_candidates(
        tmp_path,
        required_groups=frozenset({"market", "position"}),
    )

    assert candidates[0]["title"] == "收盘复盘"
    assert candidates[0]["matched_terms"]["market"] == ["大盘"]
    assert candidates[0]["matched_terms"]["position"] == ["推仓", "仓位"]
