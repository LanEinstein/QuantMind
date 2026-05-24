"""K-002 — MarketDataSnapshot append-only store (raw bytes + checksum).

Red line A.1 (R0 §3): store the complete raw payload + checksum +
verify-before-adopt, mirroring backend/broker/persistence/snapshots.py.
A hash-only variant is forbidden because a hash cannot be replayed once
the raw bytes are gone (vendor restatement / retention expiry / parser
upgrade). Vendor restatements (esp. fina_indicator_vip) become a new
append-only version that keeps the old bytes.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

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

RAW = b"ts_code,close\n000001.SZ,12.34\n600519.SH,1680.0\n"
RAW2 = b"ts_code,close\n000001.SZ,12.50\n600519.SH,1681.0\n"  # restated


def _snap(
    raw: bytes = RAW,
    *,
    vendor: str = "tushare",
    endpoint: str = "daily",
    trade_date: str = "20260522",
    version: int = 1,
) -> MarketDataSnapshot:
    return MarketDataSnapshot.create(
        vendor=vendor,
        endpoint=endpoint,
        params={"trade_date": trade_date},
        trade_date=trade_date,
        raw_payload=raw,
        encoding="csv",
        compression="none",
        fetch_time_utc=datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
        version=version,
    )


# ---------------------------------------------------------------------------
# Model self-validation
# ---------------------------------------------------------------------------


class TestModel:
    def test_create_computes_size_and_sha256(self) -> None:
        snap = _snap()
        assert snap.size == len(RAW)
        assert snap.raw_payload_sha256 == hashlib.sha256(RAW).hexdigest()
        assert len(snap.raw_payload_sha256) == 64
        assert snap.schema_version == MARKET_DATA_SNAPSHOT_SCHEMA_VERSION

    def test_model_is_frozen(self) -> None:
        snap = _snap()
        with pytest.raises(Exception):
            snap.size = 0  # type: ignore[misc]

    def test_size_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketDataSnapshot(
                vendor="tushare",
                endpoint="daily",
                params={"trade_date": "20260522"},
                trade_date="20260522",
                raw_payload=RAW,
                size=999,  # wrong
                encoding="csv",
                compression="none",
                raw_payload_sha256=hashlib.sha256(RAW).hexdigest(),
                fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
            )

    def test_sha256_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketDataSnapshot(
                vendor="tushare",
                endpoint="daily",
                params={"trade_date": "20260522"},
                trade_date="20260522",
                raw_payload=RAW,
                size=len(RAW),
                encoding="csv",
                compression="none",
                raw_payload_sha256="0" * 64,  # wrong
                fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
            )


# ---------------------------------------------------------------------------
# Roundtrip + verify-before-adopt
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_put_then_get_roundtrip(self, tmp_path: Path) -> None:
        store = SnapshotStore(root=tmp_path)
        snap = _snap()
        store.put(snap)
        loaded = store.get(snap.snapshot_id)
        assert loaded.raw_payload == RAW
        assert loaded.raw_payload_sha256 == snap.raw_payload_sha256
        assert loaded.vendor == "tushare"
        assert loaded.trade_date == "20260522"

    def test_get_unknown_id_raises(self, tmp_path: Path) -> None:
        store = SnapshotStore(root=tmp_path)
        from uuid import uuid4

        with pytest.raises(SnapshotStoreError):
            store.get(uuid4())

    def test_checksum_mismatch_fail_closed(self, tmp_path: Path) -> None:
        """Tamper the on-disk bytes — get must refuse to adopt."""
        store = SnapshotStore(root=tmp_path)
        snap = _snap()
        store.put(snap)
        # Corrupt the content-addressed payload file in place.
        payload_files = list((tmp_path / "payloads").rglob("*.bin"))
        assert len(payload_files) == 1
        payload_files[0].write_bytes(b"tampered bytes not matching sha256")
        with pytest.raises(ChecksumMismatchError):
            store.get(snap.snapshot_id)


# ---------------------------------------------------------------------------
# Append-only + restatement
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_overwrite_same_snapshot_id_rejected(self, tmp_path: Path) -> None:
        store = SnapshotStore(root=tmp_path)
        snap = _snap()
        store.put(snap)
        with pytest.raises(SnapshotOverwriteError):
            store.put(snap)  # same snapshot_id — append-only red line

    def test_fina_restatement_keeps_old_bytes(self, tmp_path: Path) -> None:
        """A fina_indicator_vip restatement is a NEW version; the old
        bytes remain byte-exact retrievable."""
        store = SnapshotStore(root=tmp_path)
        v1 = _snap(
            RAW, endpoint="fina_indicator_vip", trade_date="20251231", version=1
        )
        store.put(v1)
        v2 = _snap(
            RAW2, endpoint="fina_indicator_vip", trade_date="20251231", version=2
        )
        store.put(v2)
        # Both versions retrievable; old bytes untouched.
        assert store.get(v1.snapshot_id).raw_payload == RAW
        assert store.get(v2.snapshot_id).raw_payload == RAW2
        versions = store.versions(
            vendor="tushare", endpoint="fina_indicator_vip", trade_date="20251231"
        )
        assert [s.version for s in versions] == [1, 2]
        assert store.latest(
            vendor="tushare", endpoint="fina_indicator_vip", trade_date="20251231"
        ).raw_payload == RAW2

    def test_identical_content_dedup_by_address(self, tmp_path: Path) -> None:
        """Two snapshots with identical bytes share one payload file
        (content-addressed) but remain distinct index rows."""
        store = SnapshotStore(root=tmp_path)
        a = _snap(RAW, endpoint="daily", trade_date="20260522")
        b = _snap(RAW, endpoint="daily", trade_date="20260523")
        store.put(a)
        store.put(b)
        payload_files = list((tmp_path / "payloads").rglob("*.bin"))
        assert len(payload_files) == 1  # deduped by sha256
        assert store.get(a.snapshot_id).raw_payload == RAW
        assert store.get(b.snapshot_id).raw_payload == RAW


# ---------------------------------------------------------------------------
# Persistence across store instances (offline replay foundation)
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_reopened_store_reads_prior_snapshots(self, tmp_path: Path) -> None:
        snap = _snap()
        SnapshotStore(root=tmp_path).put(snap)
        # A fresh process / store instance reads the same data offline.
        reopened = SnapshotStore(root=tmp_path)
        assert reopened.get(snap.snapshot_id).raw_payload == RAW


# ---------------------------------------------------------------------------
# Codex P1/P2 regressions
# ---------------------------------------------------------------------------

# Non-UTF-8 bytes — what a real parquet / gzip payload looks like. The model
# explicitly allows encoding=parquet / compression=gzip, so the store must
# persist these without trying to UTF-8 decode them.
BINARY = b"\x00\x01\x02\xff\xfe\x89PNG\r\n\x1a\n\x93gzip\x00"


class TestBinaryPayload:
    def test_binary_payload_put_get_roundtrip(self, tmp_path: Path) -> None:
        """P1: model_dump(mode=json) must not choke on non-UTF-8 raw bytes."""
        store = SnapshotStore(root=tmp_path)
        snap = MarketDataSnapshot.create(
            vendor="tushare",
            endpoint="daily",
            params={"trade_date": "20260522"},
            trade_date="20260522",
            raw_payload=BINARY,
            encoding="parquet",
            compression="gzip",
            fetch_time_utc=datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
        )
        store.put(snap)
        assert store.get(snap.snapshot_id).raw_payload == BINARY


class TestVersionUniqueness:
    def test_same_key_same_version_rejected(self, tmp_path: Path) -> None:
        """P2: two snapshots for the same (vendor,endpoint,trade_date) with
        the same version (distinct ids) make versions()/latest() ambiguous."""
        store = SnapshotStore(root=tmp_path)
        store.put(_snap(RAW, endpoint="daily", trade_date="20260522", version=1))
        with pytest.raises(SnapshotOverwriteError):
            store.put(
                _snap(RAW2, endpoint="daily", trade_date="20260522", version=1)
            )


class TestStaleIndex:
    def test_get_sees_write_from_other_instance(self, tmp_path: Path) -> None:
        """P2: a long-lived reader must see a snapshot appended by another
        store instance, not fail with a stale in-memory index."""
        reader = SnapshotStore(root=tmp_path)  # constructed before the write
        writer = SnapshotStore(root=tmp_path)
        snap = _snap()
        writer.put(snap)
        # reader still has an empty in-memory index until it reloads on read.
        assert reader.get(snap.snapshot_id).raw_payload == RAW
        assert reader.latest(
            vendor="tushare", endpoint="daily", trade_date="20260522"
        ) is not None
