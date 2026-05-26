"""IntradayTriggerManifest — 30s tick lineage for offline replay (Phase U-C3).

The daily Line-2 path reuses :class:`backend.marketdata_snapshot.
SignalInputManifest`, which is shaped for the daily CSV market-frame (consumed
rows keyed by ``ts_code``). The 30s intraday trigger runner needs a **distinct**
manifest: per trigger tick it records the consumed live-quote rows + the exact
deterministic rule inputs (drawdown / ATR / recent-high / stop-level) so an
offline replay can reproduce the routed signal bit-for-bit (R0 §3 PIT contract,
§设计5 — *盘中需独立 manifest 模型*).

Lineage pins:

* ``quote_snapshot_id`` — the persisted intraday-quote :class:`MarketDataSnapshot`
  (raw bytes + checksum) the triggers consumed;
* ``daily_frame_snapshot_ids`` — the T-1 EOD frame(s) the ATR / recent-high were
  taken from;
* ``consumed_quotes`` — the exact quote rows consumed (by stable key + content
  hash, reusing :class:`backend.marketdata_snapshot.ConsumedRow`);
* ``triggers`` — the per-code rule inputs + outputs.

Import isolation (orchestration boundary, R0 §4): stdlib + pydantic + filelock
+ the **public** ``backend.marketdata_snapshot`` API only — **no**
``backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}``. Persistence
is a self-contained append-only JSONL store (mirrors
``SignalInputManifestStore``) so orchestration does not reach into module 0's
private helpers.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.marketdata_snapshot import ConsumedRow

log = structlog.get_logger(component="orchestration.intraday_manifest")

INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION = 1
"""Locked schema version. A structural change bumps this (paired with an
amendment + migration) so a stale row is detected, not silently mis-parsed."""


class IntradayManifestError(RuntimeError):
    """Raised when a manifest invariant fails (duplicate signal_id / corrupt row)."""


class IntradayTriggerRecord(BaseModel):
    """One fired intraday trigger's deterministic rule inputs + outputs.

    ``side`` is the resulting order side (``SELL`` / ``BUY``); ``kind`` is the
    :class:`backend.monitoring.intraday_triggers.IntradayTriggerKind` value for
    a SELL or ``"add"`` for an ADD. The fields capture the exact inputs the
    deterministic trigger used so a replay can recompute the verdict:

    * **SELL** is fully described by ``live_price`` / ``prev_close`` /
      ``drawdown_pct`` / ``atr`` / ``recent_high`` / ``stop_level`` +
      ``threshold_params`` (the daily closes that feed ATR / recent-high are
      pinned by the manifest's ``daily_frame_snapshot_ids``).
    * **ADD** additionally records the dip-vs-cost + sizing inputs the ADD gate
      consumes beyond the quote — ``cost_price`` / ``position_volume`` /
      ``total_assets`` / ``regime`` (the classified bear-ban verdict) /
      ``ma_long`` (the structural-breakdown reference) — so the BUY verdict is
      auditable/recomputable, not just the SELL path (codex U-C3 P2). The
      account / position state is also durably recorded in ``broker_events``;
      these fields snapshot the values the trigger actually used.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    side: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    live_price: float
    prev_close: float | None = None
    drawdown_pct: float | None = None
    atr: float | None = None
    recent_high: float | None = None
    stop_level: float | None = None
    available_volume: int = Field(ge=0)
    # ADD-only decision inputs (None for a SELL).
    cost_price: float | None = None
    position_volume: int | None = None
    total_assets: float | None = None
    regime: str | None = None
    ma_long: float | None = None
    threshold_params: dict[str, float] = Field(default_factory=dict)


class IntradayTriggerManifest(BaseModel):
    """Consumed-quote lineage + rule inputs for one intraday trigger tick."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(
        default=INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION, ge=1
    )
    signal_id: str = Field(min_length=1)
    created_at: datetime
    tick_at: datetime
    quote_snapshot_id: UUID
    daily_frame_snapshot_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    consumed_quotes: tuple[ConsumedRow, ...] = Field(default_factory=tuple)
    triggers: tuple[IntradayTriggerRecord, ...] = Field(default_factory=tuple)
    feature_code_version: str = Field(min_length=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_schema_version(self) -> IntradayTriggerManifest:
        # Fail closed on schema drift (mirrors MarketDataSnapshot): a future /
        # stale row is detected rather than silently mis-parsed (codex U-C3 P2).
        if self.schema_version != INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"intraday trigger manifest schema_version {self.schema_version} "
                f"!= {INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION}; module needs "
                "upgrade before reading"
            )
        return self


class IntradayTriggerManifestStore:
    """Append-only JSONL store keyed by ``signal_id`` (unique, immutable).

    Self-contained (filelock + json) so the orchestration layer depends only
    on the public ``backend.marketdata_snapshot`` API, never its private
    ``_jsonl`` helper. Same insert-only / no-mutation / no-delete discipline as
    the module-0 manifest stores (P1-2.A append-only red lines).
    """

    _FILE = "intraday_trigger.jsonl"
    _LOCK = "intraday_trigger.lock"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._path = self._root / self._FILE
        self._lock = FileLock(str(self._root / self._LOCK))
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, manifest: IntradayTriggerManifest) -> IntradayTriggerManifest:
        """Append a manifest; reject a duplicate signal_id (append-only)."""
        with self._lock:
            if self._find_row(manifest.signal_id) is not None:
                raise IntradayManifestError(
                    f"signal_id {manifest.signal_id!r} already stored "
                    "(append-only — intraday lineage is immutable)"
                )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        manifest.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )
        log.info(
            "intraday_trigger_manifest_put",
            signal_id=manifest.signal_id,
            quote_snapshot_id=str(manifest.quote_snapshot_id),
            consumed_quotes=len(manifest.consumed_quotes),
            triggers=len(manifest.triggers),
        )
        return manifest

    def get(self, signal_id: str) -> IntradayTriggerManifest | None:
        row = self._find_row(signal_id)
        return self._from_row(row) if row is not None else None

    def _find_row(self, signal_id: str) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        found: dict[str, Any] | None = None
        for lineno, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntradayManifestError(
                    f"corrupt JSONL row at {self._path}:{lineno}: {exc}"
                ) from exc
            if row.get("signal_id") == signal_id:
                found = row
        return found

    @staticmethod
    def _from_row(row: dict[str, Any]) -> IntradayTriggerManifest:
        # Rebuild with native UUID / datetime / tuple types — strict mode does
        # not coerce the JSON-stored str/list forms.
        consumed = tuple(
            ConsumedRow(
                snapshot_id=UUID(c["snapshot_id"]),
                row_key=c["row_key"],
                row_sha256=c["row_sha256"],
            )
            for c in row["consumed_quotes"]
        )
        triggers = tuple(
            IntradayTriggerRecord(**t) for t in row["triggers"]
        )
        return IntradayTriggerManifest(
            schema_version=row["schema_version"],
            signal_id=row["signal_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            tick_at=datetime.fromisoformat(row["tick_at"]),
            quote_snapshot_id=UUID(row["quote_snapshot_id"]),
            daily_frame_snapshot_ids=tuple(
                UUID(s) for s in row["daily_frame_snapshot_ids"]
            ),
            consumed_quotes=consumed,
            triggers=triggers,
            feature_code_version=row["feature_code_version"],
            config_hash=row["config_hash"],
        )


__all__ = [
    "INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION",
    "IntradayManifestError",
    "IntradayTriggerManifest",
    "IntradayTriggerManifestStore",
    "IntradayTriggerRecord",
]
