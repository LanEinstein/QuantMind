"""MarketDataSnapshot — point-in-time raw payload + checksum (K-002).

Red line A.1 (R0 §3): store the **complete raw payload** plus a
checksum and verify it before adopting, mirroring
:class:`backend.broker.persistence.snapshots.BrokerSnapshot`. A
hash-only variant is **forbidden** — a hash cannot reproduce the
feature input once the raw bytes are gone (vendor restatement, retention
expiry, parser upgrade). Vendor restatements (especially the silent
``fina_indicator_vip`` ones) are written as a **new append-only
version** that keeps the previous bytes (see ``store.py``).

The snapshot is byte-agnostic: the orchestration layer serialises the
vendor DataFrame into canonical bytes (``encoding`` records how —
``csv`` / ``parquet`` / ``json`` …) and hands them here. Module 0 only
persists, checksums, and returns bytes; it runs no business logic on the
payload, which is why offline ``replay`` (K-005) can rebuild the feature
matrix bit-for-bit from this layer alone.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MARKET_DATA_SNAPSHOT_SCHEMA_VERSION = 1
"""Locked schema version for marketdata snapshot rows. A structural
change bumps this (paired with an amendment + migration) so a stale row
is detected rather than silently mis-parsed."""


class MarketDataSnapshot(BaseModel):
    """A single full-market fetch persisted point-in-time.

    Provenance fields (``vendor`` / ``endpoint`` / ``params`` /
    ``trade_date`` / ``fetch_time_utc``) identify *what* was fetched and
    *when*. Payload fields (``raw_payload`` / ``size`` / ``encoding`` /
    ``compression`` / ``raw_payload_sha256``) carry the bytes and their
    integrity guard. ``version`` increments for vendor restatements of
    the same (vendor, endpoint, trade_date).

    The model is self-validating: ``size`` must equal ``len(raw_payload)``
    and ``raw_payload_sha256`` must equal ``sha256(raw_payload)``, so a
    snapshot reconstructed from storage whose bytes were tampered fails
    construction (verify-before-adopt at the model layer).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: UUID = Field(default_factory=uuid4)
    schema_version: int = Field(
        default=MARKET_DATA_SNAPSHOT_SCHEMA_VERSION, ge=1
    )

    vendor: str = Field(min_length=1)
    """Data source, e.g. ``tushare`` / ``akshare`` / ``baostock``."""
    endpoint: str = Field(min_length=1)
    """SDK endpoint, e.g. ``daily`` / ``fina_indicator_vip``."""
    params: dict[str, str] = Field(default_factory=dict)
    """Query arguments (``{"trade_date": "20260522"}`` / ``{"period": …}``)."""
    trade_date: str = Field(pattern=r"^\d{8}$")
    """Business date / report period (YYYYMMDD) the snapshot pertains to."""
    fetch_time_utc: datetime
    """When the fetch completed — must be timezone-aware (UTC)."""

    raw_payload: bytes
    """Canonical raw bytes as handed by the orchestration layer."""
    size: int = Field(ge=0)
    """``len(raw_payload)`` — guards against truncation."""
    encoding: str = Field(min_length=1)
    """How the bytes are encoded: ``csv`` / ``parquet`` / ``json`` / …"""
    compression: str = Field(min_length=1)
    """``none`` / ``gzip`` / ``zstd`` — applied over ``encoding``."""
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    """Full SHA256 hex of ``raw_payload``. 64 chars so a truncation
    immediately fails the regex."""

    version: int = Field(default=1, ge=1)
    """Append-only restatement version for the same (vendor, endpoint,
    trade_date). ``1`` is the first fetch."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form provenance (scheduler run_id, source commit, coverage
    pointer). Excluded from the checksum so adding it later never
    invalidates the stored bytes."""

    @model_validator(mode="after")
    def _check_schema_version(self) -> MarketDataSnapshot:
        if self.schema_version != MARKET_DATA_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                f"marketdata snapshot schema_version {self.schema_version} != "
                f"{MARKET_DATA_SNAPSHOT_SCHEMA_VERSION}; module needs upgrade "
                "before reading"
            )
        return self

    @model_validator(mode="after")
    def _check_payload_integrity(self) -> MarketDataSnapshot:
        if self.size != len(self.raw_payload):
            raise ValueError(
                f"size {self.size} != len(raw_payload) {len(self.raw_payload)}"
            )
        digest = hashlib.sha256(self.raw_payload).hexdigest()
        if self.raw_payload_sha256 != digest:
            raise ValueError(
                "raw_payload_sha256 mismatch: stored "
                f"{self.raw_payload_sha256} != computed {digest}"
            )
        return self

    @model_validator(mode="after")
    def _check_fetch_time_aware(self) -> MarketDataSnapshot:
        if self.fetch_time_utc.tzinfo is None:
            raise ValueError("fetch_time_utc must be timezone-aware (UTC)")
        return self

    @classmethod
    def create(
        cls,
        *,
        vendor: str,
        endpoint: str,
        params: dict[str, str],
        trade_date: str,
        raw_payload: bytes,
        encoding: str,
        compression: str,
        fetch_time_utc: datetime,
        version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> MarketDataSnapshot:
        """Build a snapshot, computing ``size`` and ``raw_payload_sha256``.

        Use this on the write path; the explicit constructor is for
        reconstruction from storage (where size/sha are already known
        and re-verified by the validators).
        """
        return cls(
            vendor=vendor,
            endpoint=endpoint,
            params=dict(params),
            trade_date=trade_date,
            raw_payload=raw_payload,
            size=len(raw_payload),
            encoding=encoding,
            compression=compression,
            raw_payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
            fetch_time_utc=fetch_time_utc,
            version=version,
            metadata=dict(metadata or {}),
        )


__all__ = [
    "MARKET_DATA_SNAPSHOT_SCHEMA_VERSION",
    "MarketDataSnapshot",
]
