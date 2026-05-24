#!/usr/bin/env python
"""K-005 — Offline bit-exact replay of a signal's feature input.

Usage:

    # Replay a signal from the snapshot store rooted at data/marketdata
    python scripts/replay_signal.py SIG-20260522-001 \\
        --root data/marketdata_snapshots

    # Print the resolved consumed rows as well
    python scripts/replay_signal.py SIG-20260522-001 \\
        --root data/marketdata_snapshots --show-rows

No network and no database are required: the replay reads stored raw
bytes + pinned consumed-row lineage from the filesystem and rebuilds the
exact rows the signal consumed (red line A.5 — R0 §3). The printed
``feature_input_digest`` is identical across replays iff the
reconstruction is bit-exact.

This CLI is read-only — it never writes snapshots, manifests, or audit.
"""

from __future__ import annotations

import argparse
import sys

from backend.marketdata_snapshot.replay import ReplayError, replay_signal


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="replay_signal",
        description="Offline bit-exact replay of a signal's feature input.",
    )
    p.add_argument("signal_id", help="The signal_id to replay.")
    p.add_argument(
        "--root",
        required=True,
        help="Root dir of the marketdata snapshot store (index.jsonl etc.).",
    )
    p.add_argument(
        "--show-rows",
        action="store_true",
        help="Print each resolved consumed row (snapshot_id, key, bytes).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = replay_signal(args.signal_id, root=args.root)
    except ReplayError as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # checksum / drift -> fail-closed, non-zero
        print(f"replay fail-closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print(f"signal_id           : {result.signal_id}")
    print(f"snapshots           : {len(result.snapshot_ids)}")
    print(f"consumed_rows       : {len(result.consumed)}")
    print(f"feature_code_version: {result.feature_code_version}")
    print(f"config_hashes       : {result.config_hashes}")
    print(f"feature_input_digest: {result.feature_input_digest}")
    if args.show_rows:
        print("--- consumed rows ---")
        for r in result.consumed:
            print(f"{r.snapshot_id} | {r.row_key} | {r.row_bytes!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
