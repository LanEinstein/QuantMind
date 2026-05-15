"""Broker persistence (E-002 / P1-2.A).

Hybrid delta + EOD snapshot persistence for the MockBroker single
account mirror. The package exposes:

* :class:`BrokerEvent` / :class:`BrokerEventType` — append-only delta
  rows written to ``broker_events`` inside a Mongo session transaction
  (replica-set required, see E-001).
* :class:`BrokerSnapshot` — full account state checkpoint written to
  ``broker_snapshots`` after each EOD pipeline run, with a deterministic
  SHA256 checksum so a corrupted checkpoint refuses to drive recovery.
* :class:`BrokerEventStore` / :class:`BrokerSnapshotStore` — append-only
  Mongo-backed stores enforcing the eight P1-2.A red lines (insert-only,
  no $set on existing rows, no delete, no truncate / drop, no schema
  rewrites, no rebalance, no checksum patch, schema_version monotonic).
* :func:`recover_state` — load latest snapshot, replay events strictly
  newer, verify checksum; fail-closed when checksum mismatches.

LLM red line: nothing in this module imports
``backend.{llm,agents,mirofish}``. The Builder / scheduler call the
stores via dependency injection. Test isolation probe lives in
``tests/test_broker_persistence_isolation.py``.
"""

from backend.broker.persistence.checksum import (
    canonical_state_payload,
    compute_snapshot_checksum,
)
from backend.broker.persistence.events import (
    BROKER_EVENT_SCHEMA_VERSION,
    BrokerEvent,
    BrokerEventType,
)
from backend.broker.persistence.recovery import (
    ChecksumMismatchError,
    RecoveredState,
    RecoveryError,
    recover_state,
)
from backend.broker.persistence.snapshots import (
    BROKER_SNAPSHOT_SCHEMA_VERSION,
    BrokerSnapshot,
    BrokerSnapshotPosition,
)
from backend.broker.persistence.store import (
    BrokerEventStore,
    BrokerPersistenceError,
    BrokerSnapshotStore,
)

__all__ = [
    "BROKER_EVENT_SCHEMA_VERSION",
    "BROKER_SNAPSHOT_SCHEMA_VERSION",
    "BrokerEvent",
    "BrokerEventStore",
    "BrokerEventType",
    "BrokerPersistenceError",
    "BrokerSnapshot",
    "BrokerSnapshotPosition",
    "BrokerSnapshotStore",
    "ChecksumMismatchError",
    "RecoveredState",
    "RecoveryError",
    "canonical_state_payload",
    "compute_snapshot_checksum",
    "recover_state",
]
