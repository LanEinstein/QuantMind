"""Render a transcript evidence span without rewriting its source text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_quote(observation_path: Path, evidence_id: str) -> dict[str, Any]:
    """Rebuild one quote so source text remains the only quote authority."""
    observation = _load_json(observation_path)
    evidence = next(
        (
            item
            for item in observation.get("evidence", [])
            if item.get("evidence_id") == evidence_id
        ),
        None,
    )
    if evidence is None:
        raise ValueError(f"unknown evidence_id: {evidence_id}")
    source_ref = str(evidence.get("source_ref", "")).split("#", 1)[0]
    transcript_path = Path(source_ref)
    transcript_id = transcript_path.stem
    if transcript_id != str(observation.get("aweme_id")):
        raise ValueError("transcript aweme_id does not match observation")
    span = evidence.get("transcript_span")
    if not isinstance(span, dict):
        raise ValueError("evidence has no transcript span")
    start = int(span["sentence_index"])
    # The schema stores single-sentence spans as an explicit null end index,
    # so a present-but-null field means the same thing as an absent one.
    raw_end = span.get("end_sentence_index")
    end = start if raw_end is None else int(raw_end)
    transcript = _load_json(transcript_path)
    sentences = transcript.get("sentences") or []
    if start < 0 or end < start or end >= len(sentences):
        raise ValueError("transcript span is out of bounds")
    quote = "".join(str(sentences[index]["text"]) for index in range(start, end + 1))
    if quote != str(span.get("raw_text", "")):
        raise ValueError("evidence raw_text does not match transcript sentences")
    return {
        "quote": quote,
        "aweme_id": str(observation["aweme_id"]),
        "evidence_id": evidence_id,
        "statement_ids": [
            item["statement_id"]
            for item in observation.get("statements", [])
            if evidence_id in item.get("evidence_ids", [])
        ],
        "interpretation_ids": [
            item["interpretation_id"]
            for item in observation.get("interpretations", [])
            if evidence_id in item.get("evidence_ids", [])
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation", type=Path)
    parser.add_argument("evidence_id")
    return parser


def main() -> None:
    args = _parser().parse_args()
    print(
        json.dumps(
            render_quote(args.observation, args.evidence_id), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
