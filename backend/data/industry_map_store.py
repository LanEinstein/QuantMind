"""O-003 PIT-pinned industry (code→sector) map store.

The MiroFish sector forecast (O-002) scores sectors; turning that into a
per-code advisory (O-003) needs a code→sector map. To keep the advisory
re-rank **replayable** (R0 red line ① — PIT reproducibility), the map is
not fetched live at selection time: the 17:00 EOD pipeline persists the
exact industry map it used (the same one that built the digest's sector
heat) as a dated artifact, and the next morning's Line-1 advisory loads
the map **co-dated with the forecast it consumes**. Replaying the same
frame + forecast therefore re-derives the same advisory signals, even if
Tushare's live industry labels later drift.

File-based, one JSON per trade date, deterministic key order. Fail-open
by construction: a missing / corrupt artifact loads as ``{}`` so the
advisory simply degrades to the pure-quant path (never raises).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

import structlog

log = structlog.get_logger(component="data.industry_map_store")

# Strict allowlist for the dated filename. trade_date is a trusted internal
# ISO value, but an allowlist (not a blocklist replace) is the correct
# boundary guard so a non-ISO value can never produce an unexpected path.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IndustryMapStore:
    """Persist / load the dated code→sector map used for advisory re-rank."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, trade_date: str) -> Path | None:
        if not _DATE_RE.fullmatch(trade_date):
            log.warning("industry_map_bad_date", trade_date=trade_date)
            return None
        return self._root / f"{trade_date}.json"

    def save(self, trade_date: str, mapping: Mapping[str, str]) -> None:
        """Persist ``mapping`` for ``trade_date`` (overwrites; key-sorted).

        Best-effort: a write failure is logged and swallowed — the forecast
        evidence is the primary artifact, and a missing industry map only
        means the next day's advisory degrades to pure-quant.
        """
        path = self._path(trade_date)
        if path is None:
            return
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            ordered = {k: mapping[k] for k in sorted(mapping)}
            path.write_text(
                json.dumps(ordered, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 — artifact write best-effort
            log.warning(
                "industry_map_save_failed", trade_date=trade_date, error=str(exc)
            )

    def load(self, trade_date: str) -> dict[str, str]:
        """Load the pinned map for ``trade_date`` (``{}`` if absent/corrupt)."""
        path = self._path(trade_date)
        if path is None or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — fail-open to pure quant
            log.warning(
                "industry_map_load_failed", trade_date=trade_date, error=str(exc)
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)
        }


__all__ = ["IndustryMapStore"]
