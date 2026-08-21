"""Co-occurrence retrieval over transcript sentences for cross-card semantics.

Serves work unit F (see docs/research/yeren-system/
m3-post-candidate-e-execution-plan-2026-08-21.md): four semantic questions that
span several playbook cards. Retrieval reads `sentences` only, never the
transcript-level `text` field, because 857 of 1,110 transcripts have a `text`
field that disagrees with their own sentence list.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.yeren_research.inventory import read_jsonl


@dataclass(frozen=True)
class Query:
    """One retrieval question: left terms, optional right terms, sentence window."""

    name: str
    left: tuple[str, ...]
    right: tuple[str, ...] = ()
    window: int = 1
    note: str = ""


@dataclass(frozen=True)
class Hit:
    """A minimal-width sentence span satisfying a query, quoted verbatim."""

    aweme_id: str
    published_at: str
    start_index: int
    end_index: int
    text: str
    matched: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    """Hits for one query, plus the counts a reader needs to judge coverage."""

    query: Query
    hits: tuple[Hit, ...] = field(default_factory=tuple)

    @property
    def video_count(self) -> int:
        return len({hit.aweme_id for hit in self.hits})


def _load_published_at(corpus_root: Path) -> dict[str, str]:
    metadata_path = corpus_root / "metadata.jsonl"
    if not metadata_path.exists():
        return {}
    return {
        str(row["aweme_id"]): str(row.get("published_at") or "")
        for row in read_jsonl(metadata_path)
    }


def _sentences(transcript: dict[str, Any]) -> list[str]:
    return [str(item.get("text") or "") for item in transcript.get("sentences") or []]


def _match_span(span: str, query: Query) -> tuple[str, ...] | None:
    left = [term for term in query.left if term in span]
    if not left:
        return None
    if not query.right:
        return tuple(left)
    right = [term for term in query.right if term in span]
    if not right:
        return None
    return tuple(left) + tuple(right)


def _search_sentences(
    sentences: list[str], query: Query, aweme_id: str, published_at: str
) -> list[Hit]:
    """Report a match at its narrowest window, but keep wider windows that add terms.

    Suppressing every wider window that merely contains a narrower hit would drop
    real evidence: a two-sentence span can satisfy the query with a *different*
    term set than the single sentence inside it. So a wider window is dropped only
    when its matched terms are already covered by an enclosed narrower hit.
    """
    hits: list[Hit] = []
    covered: list[tuple[int, int, frozenset[str]]] = []
    for width in range(1, query.window + 1):
        for start in range(0, len(sentences) - width + 1):
            end = start + width - 1
            span = "".join(sentences[start : end + 1])
            matched = _match_span(span, query)
            if matched is None:
                continue
            terms = frozenset(matched)
            if any(
                start <= lo and hi <= end and terms <= seen for lo, hi, seen in covered
            ):
                continue
            covered.append((start, end, terms))
            hits.append(
                Hit(
                    aweme_id=aweme_id,
                    published_at=published_at,
                    start_index=start,
                    end_index=end,
                    text=span,
                    matched=matched,
                )
            )
    return hits


def search_corpus(corpus_root: Path, query: Query) -> tuple[Hit, ...]:
    """Return every minimal-width span in the corpus that satisfies `query`."""
    if query.window < 1:
        raise ValueError(f"window must be >= 1, got {query.window}")
    published = _load_published_at(corpus_root)
    hits: list[Hit] = []
    for path in sorted((corpus_root / "transcripts").glob("*.json")):
        transcript = json.loads(path.read_text(encoding="utf-8"))
        hits.extend(
            _search_sentences(
                _sentences(transcript), query, path.stem, published.get(path.stem, "")
            )
        )
    return tuple(
        sorted(hits, key=lambda hit: (hit.published_at, hit.aweme_id, hit.start_index))
    )


def run_queries(
    corpus_root: Path, queries: tuple[Query, ...] | None = None
) -> tuple[QueryResult, ...]:
    # Imported lazily: the query table imports Query from this module.
    if queries is None:
        from scripts.yeren_research.cross_card_semantics_queries import QUERIES

        queries = QUERIES
    return tuple(
        QueryResult(query=q, hits=search_corpus(corpus_root, q)) for q in queries
    )


def _as_dict(result: QueryResult) -> dict[str, Any]:
    return {
        "name": result.query.name,
        "note": result.query.note,
        "left_terms": list(result.query.left),
        "right_terms": list(result.query.right),
        "window": result.query.window,
        "hit_count": len(result.hits),
        "video_count": result.video_count,
        "hits": [
            {
                "aweme_id": hit.aweme_id,
                "published_at": hit.published_at,
                "start_index": hit.start_index,
                "end_index": hit.end_index,
                "matched": list(hit.matched),
                "text": hit.text,
            }
            for hit in result.hits
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path("data/yeren_corpus"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = run_queries(args.corpus_root)
    payload = {
        "corpus_root": str(args.corpus_root),
        "transcript_count": len(
            list((args.corpus_root / "transcripts").glob("*.json"))
        ),
        "queries": [_as_dict(result) for result in results],
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for result in results:
        name = result.query.name
        print(f"{name}: {len(result.hits)} hits / {result.video_count} videos")


if __name__ == "__main__":
    main()
