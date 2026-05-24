"""Point-in-time market data snapshots (module 0, Phase K).

The PIT-reproducible foundation for the full-market data path: store raw
bytes + checksum (K-002), coverage + consumed-row lineage (K-003),
pinned adjust-factor artifacts (K-004), and offline bit-exact replay
(K-005). Backtest / 45-day shadow / live signal explanation all read by
``snapshot_id``.

Import isolation (R0 §3/§7): this package must not import
``backend.{llm,agents,mirofish}``. It depends only on the standard
library, pydantic, structlog, and filelock.
"""

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
    "MARKET_DATA_SNAPSHOT_SCHEMA_VERSION",
    "ChecksumMismatchError",
    "MarketDataSnapshot",
    "SnapshotOverwriteError",
    "SnapshotStore",
    "SnapshotStoreError",
]
