"""Tests for source-faithful transcript quote rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.yeren_research.evidence_quote import render_quote


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    transcript_path = tmp_path / "v1.json"
    transcript_path.write_text(
        json.dumps(
            {"sentences": [{"text": "先"}, {"text": "试错。"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    observation_path = tmp_path / "observation.json"
    observation_path.write_text(
        json.dumps(
            {
                "aweme_id": "v1",
                "evidence": [
                    {
                        "evidence_id": "quote-1",
                        "source_ref": str(transcript_path),
                        "transcript_span": {
                            "sentence_index": 0,
                            "end_sentence_index": 1,
                            "raw_text": "先试错。",
                        },
                    }
                ],
                "statements": [
                    {"statement_id": "statement-1", "evidence_ids": ["quote-1"]}
                ],
                "interpretations": [
                    {
                        "interpretation_id": "interpretation-1",
                        "evidence_ids": ["quote-1"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return observation_path, transcript_path


def test_render_quote_rejects_out_of_bounds_span(tmp_path: Path) -> None:
    observation_path, _ = _fixture(tmp_path)
    payload = json.loads(observation_path.read_text(encoding="utf-8"))
    payload["evidence"][0]["transcript_span"]["end_sentence_index"] = 2
    observation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="out of bounds"):
        render_quote(observation_path, "quote-1")


def test_render_quote_rejects_aweme_mismatch(tmp_path: Path) -> None:
    observation_path, transcript_path = _fixture(tmp_path)
    payload = json.loads(observation_path.read_text(encoding="utf-8"))
    payload["evidence"][0]["source_ref"] = str(transcript_path.with_name("other.json"))
    observation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        render_quote(observation_path, "quote-1")


def test_render_quote_rejects_text_mismatch(tmp_path: Path) -> None:
    observation_path, _ = _fixture(tmp_path)
    payload = json.loads(observation_path.read_text(encoding="utf-8"))
    payload["evidence"][0]["transcript_span"]["raw_text"] = "手打文本"
    observation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        render_quote(observation_path, "quote-1")


def test_render_quote_accepts_null_end_index(tmp_path: Path) -> None:
    observation_path, _ = _fixture(tmp_path)
    payload = json.loads(observation_path.read_text(encoding="utf-8"))
    span = payload["evidence"][0]["transcript_span"]
    span["end_sentence_index"] = None
    span["raw_text"] = "先"
    observation_path.write_text(json.dumps(payload), encoding="utf-8")

    assert render_quote(observation_path, "quote-1")["quote"] == "先"
