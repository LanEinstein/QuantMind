"""O-005 dated per-sector daily return store (calibration input).

The 17:00 EOD pipeline pins each trading day's ``{sector: mean_pct_chg}``
(straight from the digest's deterministic sector heat) as a dated
artifact. The forecast calibration ledger (O-005) later sums these daily
sector returns over a forecast's horizon window to score the forecast
against realized outcomes — deterministically and replayably (same days
pinned → same realized returns).

File-based, one JSON per trade date, deterministic key order. Fail-open:
a missing / corrupt artifact loads as ``{}`` (the ledger then cannot
score that window yet and retries next day).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path

import structlog

log = structlog.get_logger(component="data.sector_return_store")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SectorReturnStore:
    """Persist / load the dated per-sector daily return map."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, trade_date: str) -> Path | None:
        if not _DATE_RE.fullmatch(trade_date):
            log.warning("sector_return_bad_date", trade_date=trade_date)
            return None
        return self._root / f"{trade_date}.json"

    def save(self, trade_date: str, returns: Mapping[str, float]) -> None:
        """Persist ``returns`` for ``trade_date`` (overwrites; key-sorted)."""
        path = self._path(trade_date)
        if path is None:
            return
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            ordered = {
                k: float(returns[k])
                for k in sorted(returns)
                if math.isfinite(float(returns[k]))
            }
            path.write_text(
                json.dumps(ordered, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 — artifact write best-effort
            log.warning(
                "sector_return_save_failed", trade_date=trade_date, error=str(exc)
            )

    def load(self, trade_date: str) -> dict[str, float]:
        """Load the pinned returns for ``trade_date`` (``{}`` if absent)."""
        path = self._path(trade_date)
        if path is None or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning(
                "sector_return_load_failed", trade_date=trade_date, error=str(exc)
            )
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in data.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if isinstance(k, str) and math.isfinite(fv):
                out[k] = fv
        return out


__all__ = ["SectorReturnStore"]
