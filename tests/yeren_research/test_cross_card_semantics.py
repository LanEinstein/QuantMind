"""Retrieval must quote sentences, because transcript `text` fields are unreliable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.yeren_research.cross_card_semantics import Query, search_corpus


def _write_corpus(root: Path, videos: dict[str, list[str]], *, text: str = "") -> None:
    transcripts = root / "transcripts"
    transcripts.mkdir(parents=True)
    metadata_lines = []
    for index, (aweme_id, sentences) in enumerate(videos.items()):
        payload = {
            "asr_model": "test",
            "text": text,
            "sentences": [{"start_ms": 0, "end_ms": 1, "text": s} for s in sentences],
        }
        (transcripts / f"{aweme_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        metadata_lines.append(
            json.dumps(
                {"aweme_id": aweme_id, "published_at": f"2026-01-0{index + 1}"},
                ensure_ascii=False,
            )
        )
    (root / "metadata.jsonl").write_text(
        "\n".join(metadata_lines) + "\n", encoding="utf-8"
    )


def test_co_occurrence_within_one_sentence(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["五日线是加权算出来的", "无关句子"]})
    query = Query(name="q1", left=("五日线",), right=("加权",))
    hits = search_corpus(tmp_path, query)
    assert len(hits) == 1
    assert hits[0].aweme_id == "a1"
    assert hits[0].start_index == 0
    assert hits[0].end_index == 0
    assert hits[0].text == "五日线是加权算出来的"
    assert hits[0].matched == ("五日线", "加权")
    assert hits[0].published_at == "2026-01-01"


def test_left_only_query_matches_without_right_terms(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["这里出现 EMA 这个词"]})
    hits = search_corpus(tmp_path, Query(name="q", left=("EMA",)))
    assert len(hits) == 1
    assert hits[0].matched == ("EMA",)


def test_adjacent_sentences_need_window_two(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["趋势已经不行了", "这个逻辑就是失效了"]})
    query = Query(name="q4", left=("趋势",), right=("失效",))
    assert search_corpus(tmp_path, query) == ()
    windowed = search_corpus(
        tmp_path, Query(name="q4", left=("趋势",), right=("失效",), window=2)
    )
    assert len(windowed) == 1
    assert windowed[0].start_index == 0
    assert windowed[0].end_index == 1
    assert windowed[0].text == "趋势已经不行了这个逻辑就是失效了"


def test_narrower_hit_suppresses_overlapping_wider_window(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["趋势失效了", "补一句", "再补一句"]})
    hits = search_corpus(
        tmp_path, Query(name="q4", left=("趋势",), right=("失效",), window=2)
    )
    assert len(hits) == 1
    assert (hits[0].start_index, hits[0].end_index) == (0, 0)


def test_text_field_is_ignored(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["无关句子"]}, text="五日线是加权算出来的")
    hits = search_corpus(tmp_path, Query(name="q1", left=("五日线",), right=("加权",)))
    assert hits == ()


def test_no_match_returns_empty(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["完全无关"]})
    assert search_corpus(tmp_path, Query(name="q", left=("均线",))) == ()


def test_transcript_without_metadata_is_kept_with_empty_published_at(
    tmp_path: Path,
) -> None:
    _write_corpus(tmp_path, {"a1": ["均线"]})
    orphan = {"asr_model": "test", "text": "", "sentences": [{"text": "均线"}]}
    (tmp_path / "transcripts" / "a2.json").write_text(
        json.dumps(orphan, ensure_ascii=False), encoding="utf-8"
    )
    hits = search_corpus(tmp_path, Query(name="q", left=("均线",)))
    assert {hit.aweme_id for hit in hits} == {"a1", "a2"}
    assert next(hit for hit in hits if hit.aweme_id == "a2").published_at == ""


def test_window_must_be_positive(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {"a1": ["均线"]})
    with pytest.raises(ValueError):
        search_corpus(tmp_path, Query(name="q", left=("均线",), window=0))


def test_wider_window_kept_when_it_adds_a_new_matched_term(tmp_path: Path) -> None:
    """A two-sentence span can satisfy the query with terms the inner sentence lacks."""
    _write_corpus(
        tmp_path, {"a1": ["这就是我们的套利空间啊", "大概也就是三到五个点啊"]}
    )
    query = Query(name="q3", left=("个点", "空间"), right=("套利", "就走"), window=2)
    hits = search_corpus(tmp_path, query)
    spans = {(hit.start_index, hit.end_index): hit.matched for hit in hits}
    assert (0, 0) in spans and spans[(0, 0)] == ("空间", "套利")
    assert (0, 1) in spans and spans[(0, 1)] == ("个点", "空间", "套利")


def test_cross_sentence_join_can_form_a_term_split_by_asr(tmp_path: Path) -> None:
    """Intended: FunASR splits phrases mid-word, so joined spans must still match."""
    _write_corpus(tmp_path, {"a1": ["五日", "线上穿二十日线"]})
    hits = search_corpus(tmp_path, Query(name="q", left=("五日线",), window=2))
    assert len(hits) == 1
    assert (hits[0].start_index, hits[0].end_index) == (0, 1)
    assert hits[0].text == "五日线上穿二十日线"
