"""Command line entry point for offline Yeren research artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scripts.yeren_research.corpus import TERM_GROUPS, find_candidates
from scripts.yeren_research.financial import build_financial_decision_bundle
from scripts.yeren_research.inventory import build_asset_inventory
from scripts.yeren_research.market import build_market_bundles, write_new_json
from scripts.yeren_research.news import audit_local_news
from scripts.yeren_research.schema import (
    EvidenceBundle,
    HypothesisRevision,
    VideoObservation,
)

DEFAULT_CORPUS_ROOT = Path("data/yeren_corpus")
DEFAULT_PIT_ROOT = Path("data/marketdata_pit")
DEFAULT_RESEARCH_ROOT = Path("data/yeren_research")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline evidence tools for Yeren system reconstruction"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="audit corpus and PIT assets")
    audit.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    audit.add_argument("--pit-root", type=Path, default=DEFAULT_PIT_ROOT)
    audit.add_argument("--output", type=Path)

    audit_news = subparsers.add_parser(
        "audit-news", help="audit the local latest-news Mongo collection"
    )
    audit_news.add_argument("--uri", default="mongodb://127.0.0.1:27017")
    audit_news.add_argument("--database", default="quantmind")
    audit_news.add_argument("--collection", default="news_articles")
    audit_news.add_argument("--output", type=Path)

    candidates = subparsers.add_parser(
        "candidates", help="find transcript candidates for GPT review"
    )
    candidates.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    candidates.add_argument(
        "--require",
        action="append",
        choices=sorted(TERM_GROUPS),
        default=[],
        help="keyword group that must occur; repeat for intersections",
    )
    candidates.add_argument("--year", type=int)
    candidates.add_argument("--output", type=Path)

    schema = subparsers.add_parser("schema", help="export a JSON schema")
    schema.add_argument("kind", choices=("observation", "hypothesis", "bundle"))
    schema.add_argument("--output", type=Path)

    validate = subparsers.add_parser("validate", help="validate one research JSON")
    validate.add_argument("kind", choices=("observation", "hypothesis", "bundle"))
    validate.add_argument("path", type=Path)

    bundle = subparsers.add_parser(
        "bundle-market", help="partition PIT market evidence around a cutoff"
    )
    bundle.add_argument("--case-id", required=True)
    bundle.add_argument("--video-id", action="append", required=True)
    bundle.add_argument("--decision-cutoff", type=datetime.fromisoformat, required=True)
    bundle.add_argument("--start-date", required=True)
    bundle.add_argument("--end-date", required=True)
    bundle.add_argument("--endpoint", action="append")
    bundle.add_argument("--code", action="append", default=[])
    bundle.add_argument("--pit-root", type=Path, default=DEFAULT_PIT_ROOT)
    bundle.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)

    financial = subparsers.add_parser(
        "bundle-financial", help="build date-gated financial decision evidence"
    )
    financial.add_argument("--case-id", required=True)
    financial.add_argument("--video-id", action="append", required=True)
    financial.add_argument(
        "--decision-cutoff", type=datetime.fromisoformat, required=True
    )
    financial.add_argument("--code", action="append", required=True)
    financial.add_argument("--lookback-periods", type=int, default=8)
    financial.add_argument("--pit-root", type=Path, default=DEFAULT_PIT_ROOT)
    financial.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    return parser


def _emit(value: Any, output: Path | None) -> None:
    if output is not None:
        write_new_json(output, value)
        print(output)
        return
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _model(kind: str) -> type[BaseModel]:
    models: dict[str, type[BaseModel]] = {
        "observation": VideoObservation,
        "hypothesis": HypothesisRevision,
        "bundle": EvidenceBundle,
    }
    return models[kind]


def main() -> None:
    args = _parser().parse_args()
    if args.command == "audit":
        _emit(build_asset_inventory(args.corpus_root, args.pit_root), args.output)
        return
    if args.command == "audit-news":
        _emit(
            audit_local_news(
                uri=args.uri,
                database=args.database,
                collection=args.collection,
            ),
            args.output,
        )
        return
    if args.command == "candidates":
        value = find_candidates(
            args.corpus_root,
            required_groups=frozenset(args.require),
            year=args.year,
        )
        _emit(value, args.output)
        return
    if args.command == "schema":
        _emit(_model(args.kind).model_json_schema(), args.output)
        return
    if args.command == "validate":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        _emit(_model(args.kind).model_validate(payload), None)
        return
    if args.command == "bundle-market":
        decision, outcome = build_market_bundles(
            case_id=args.case_id,
            video_ids=args.video_id,
            decision_cutoff=args.decision_cutoff,
            pit_root=args.pit_root,
            start_date=args.start_date,
            end_date=args.end_date,
            endpoints=tuple(args.endpoint or ("daily",)),
            codes=args.code,
        )
        write_new_json(
            args.research_root / "decision_bundles" / f"{args.case_id}.json",
            decision,
        )
        write_new_json(
            args.research_root / "outcome_bundles" / f"{args.case_id}.json",
            outcome,
        )
        print(args.case_id)
        return
    if args.command == "bundle-financial":
        bundle = build_financial_decision_bundle(
            case_id=args.case_id,
            video_ids=tuple(args.video_id),
            decision_cutoff=args.decision_cutoff,
            pit_root=args.pit_root,
            codes=tuple(args.code),
            lookback_periods=args.lookback_periods,
        )
        write_new_json(
            args.research_root / "decision_bundles" / f"{args.case_id}-financial.json",
            bundle,
        )
        print(args.case_id)


if __name__ == "__main__":
    main()
