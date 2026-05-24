"""Point-in-time market data snapshots (module 0, Phase K).

The PIT-reproducible foundation for the full-market data path:

* **K-002** ``MarketDataSnapshot`` + ``SnapshotStore`` — store raw bytes
  + checksum, append-only, verify-before-adopt.
* **K-003** ``CoverageManifest`` (requested vs delivered universe) +
  ``SignalInputManifest`` (consumed-row lineage).
* **K-004** ``AdjustFactorArtifact`` — pinned factor table for bit-exact
  qfq/hfq/raw reconstruction (per-use policy).
* **K-005** ``Replayer`` / ``replay_signal`` — offline bit-exact replay
  of a signal's feature input by ``signal_id``.

Backtest / 45-day shadow / live signal explanation all read by
``snapshot_id`` through :class:`Replayer`.

Import isolation (R0 §3/§7): this package is a **pure** storage/replay
layer. It must not import ``backend.{llm,agents,mirofish}`` (nor any
other ``backend.*`` subpackage); the orchestration layer fetches data
(via ``backend.data.tushare_client``) and hands payloads in. It depends
only on the standard library, pydantic, structlog, and filelock.
"""

from backend.marketdata_snapshot.adjust import (
    ADJUST_FACTOR_ARTIFACT_SCHEMA_VERSION,
    AdjustFactorArtifact,
    AdjustFactorStore,
    AdjustPolicy,
    AdjustUse,
    policy_for_use,
)
from backend.marketdata_snapshot.coverage import (
    COVERAGE_MANIFEST_SCHEMA_VERSION,
    CoverageManifest,
    CoverageStore,
)
from backend.marketdata_snapshot.replay import (
    CsvRowParser,
    Replayer,
    ReplayError,
    ReplayResult,
    RowParser,
    replay_signal,
)
from backend.marketdata_snapshot.signal_input_manifest import (
    SIGNAL_INPUT_MANIFEST_SCHEMA_VERSION,
    ConsumedRow,
    ResolvedRow,
    SignalInputError,
    SignalInputManifest,
    SignalInputManifestStore,
    build_consumed_row,
    row_sha256,
)
from backend.marketdata_snapshot.snapshot import (
    MARKET_DATA_SNAPSHOT_SCHEMA_VERSION,
    MarketDataSnapshot,
)
from backend.marketdata_snapshot.store import (
    ChecksumMismatchError,
    SnapshotOverwriteError,
    SnapshotStore,
    SnapshotStoreError,
)

__all__ = [
    # schema versions
    "ADJUST_FACTOR_ARTIFACT_SCHEMA_VERSION",
    "COVERAGE_MANIFEST_SCHEMA_VERSION",
    "MARKET_DATA_SNAPSHOT_SCHEMA_VERSION",
    "SIGNAL_INPUT_MANIFEST_SCHEMA_VERSION",
    # K-002 snapshot
    "ChecksumMismatchError",
    "MarketDataSnapshot",
    "SnapshotOverwriteError",
    "SnapshotStore",
    "SnapshotStoreError",
    # K-003 coverage + lineage
    "ConsumedRow",
    "CoverageManifest",
    "CoverageStore",
    "ResolvedRow",
    "SignalInputError",
    "SignalInputManifest",
    "SignalInputManifestStore",
    "build_consumed_row",
    "row_sha256",
    # K-004 adjust
    "AdjustFactorArtifact",
    "AdjustFactorStore",
    "AdjustPolicy",
    "AdjustUse",
    "policy_for_use",
    # K-005 replay
    "CsvRowParser",
    "ReplayError",
    "ReplayResult",
    "Replayer",
    "RowParser",
    "replay_signal",
]
