"""Point-in-time CSI300 constituent-weight reader (R2-3 / T1).

The benchmark-relative arm tilts off the CSI300 *constituent weights*: the
portfolio starts at the benchmark weights and adds a bounded active overlay, so
beta ≈ 1 and the round-1 "can't track a cap-weighted bull" failure is a
construction property, not a factor bet.

Weights must be point-in-time. The round-2 ingest stored ``index_weight`` once
per month, queried by month range, so each monthly snapshot carries rows for
MULTIPLE publish dates (e.g. 2024-01-02 and 2024-01-31), each its own ~100%
cross-section. ``asof(d)`` therefore selects the latest publish date STRICTLY
before ``d`` (a built-in availability lag — a weight published on ``d`` is not
used to trade on ``d``) and normalises that cross-section to sum 1.0. CSI300
weights have no pre-2016 data (vendor limit), so a 2015 decision date returns
``{}`` (the caller skips that rebalance).

Keyed by full ``ts_code`` (``con_code``) to join the round-2 panel's ``ts_code``.
dtype-safe read (the round-2 trap): con_code / trade_date kept as str. Pure +
deterministic; reads bytes from the SnapshotStore only.
"""

from __future__ import annotations

import io
import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

from .ingest_round2_data import EP_INDEX_WEIGHT

VENDOR = "tushare"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD


def index_weight_keys(snapshot_root: str) -> tuple[str, ...]:
    """All stored ``index_weight`` monthly snapshot keys (from the index)."""
    index_path = Path(snapshot_root) / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"snapshot index not found: {index_path}")
    keys: set[str] = set()
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("endpoint") == EP_INDEX_WEIGHT:
                keys.add(str(rec["trade_date"]))
    return tuple(sorted(keys))


@dataclass(frozen=True)
class BenchmarkWeightsPIT:
    """Per-publish-date normalised CSI300 weights (immutable, PIT)."""

    by_publish: dict[str, dict[str, float]]
    publish_dates: tuple[str, ...]

    @classmethod
    def build(
        cls, store: SnapshotStore, month_keys: Sequence[str]
    ) -> BenchmarkWeightsPIT:
        """Assemble per-publish-date weight cross-sections from monthly snapshots.

        Each publish date's rows are normalised to sum 1.0 (raw weights are in
        percent). Rows with a non-finite / non-positive weight are dropped
        fail-closed. A missing month snapshot raises :class:`FileNotFoundError`.
        """
        staged: dict[str, dict[str, float]] = defaultdict(dict)
        for key in month_keys:
            snapshot = store.latest(
                vendor=VENDOR, endpoint=EP_INDEX_WEIGHT, trade_date=key
            )
            if snapshot is None:
                raise FileNotFoundError(f"no index_weight snapshot for {key}")
            frame = pd.read_csv(
                io.StringIO(snapshot.raw_payload.decode("utf-8")),
                dtype={"con_code": str, "trade_date": str},
            )
            if "weight" not in frame.columns:
                raise ValueError(f"index_weight {key} snapshot has no 'weight' column")
            weights = pd.to_numeric(frame["weight"], errors="coerce")
            for con_code, publish, weight in zip(
                frame["con_code"].astype(str),
                frame["trade_date"].astype(str),
                weights,
                strict=True,
            ):
                if not _DATE_RE.match(publish):
                    continue
                # Drop non-finite (NaN / ±inf) or non-positive weights (codex P3
                # — ``!= self`` alone would let a malformed ``inf`` through).
                if not math.isfinite(weight) or weight <= 0:
                    continue
                staged[publish][con_code.strip()] = float(weight)
        normalised: dict[str, dict[str, float]] = {}
        for publish, raw in staged.items():
            total = sum(raw.values())
            if total <= 0:
                continue
            normalised[publish] = {c: w / total for c, w in raw.items()}
        return cls(
            by_publish=normalised,
            publish_dates=tuple(sorted(normalised)),
        )

    def asof(self, decision_date: str) -> dict[str, float]:
        """Normalised benchmark weights known as of ``decision_date``.

        The latest publish date strictly before ``decision_date`` (availability
        lag); ``{}`` when none exists (e.g. pre-2016, or before the first publish).
        """
        latest = ""
        for publish in self.publish_dates:
            if publish < decision_date:
                latest = publish
            else:
                break
        return dict(self.by_publish[latest]) if latest else {}


__all__ = ["BenchmarkWeightsPIT", "index_weight_keys"]
