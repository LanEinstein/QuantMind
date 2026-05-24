"""K-003 — SignalInputManifest: consumed-row lineage for exact replay.

Red line A.3 (R0 §3): per signal_id, record {snapshot_ids, consumed-row
hashes, feature_code_version, config_hashes, join/filter params} so
replay rebuilds **exactly** the rows the signal consumed — not the wider
snapshot superset — and detects drift (a row whose bytes changed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from backend.marketdata_snapshot.signal_input_manifest import (
    SignalInputError,
    SignalInputManifest,
    SignalInputManifestStore,
    build_consumed_row,
    row_sha256,
)

SNAP = uuid4()
# Full snapshot rows (5 stocks) keyed by ts_code -> canonical row bytes.
FULL_ROWS: dict[str, bytes] = {
    "000001.SZ": b"000001.SZ,12.34",
    "600519.SH": b"600519.SH,1680.0",
    "300750.SZ": b"300750.SZ,210.5",
    "000002.SZ": b"000002.SZ,9.87",
    "601318.SH": b"601318.SH,45.6",
}
# The signal consumed only 2 of the 5 rows.
CONSUMED_KEYS = ("000001.SZ", "300750.SZ")


def _manifest(signal_id: str = "SIG-20260522-001") -> SignalInputManifest:
    rows = tuple(
        build_consumed_row(SNAP, key, FULL_ROWS[key]) for key in CONSUMED_KEYS
    )
    return SignalInputManifest(
        signal_id=signal_id,
        created_at=datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
        snapshot_ids=(SNAP,),
        consumed_rows=rows,
        feature_code_version="alpha158-subset@v1",
        config_hashes={"screening": "abc123", "budget": "def456"},
        join_filter_params={"min_amount_20d": 2e8},
    )


class TestRowHashing:
    def test_row_sha256_deterministic(self) -> None:
        assert row_sha256(b"x") == row_sha256(b"x")
        assert len(row_sha256(b"x")) == 64

    def test_build_consumed_row_records_key_and_hash(self) -> None:
        cr = build_consumed_row(SNAP, "000001.SZ", FULL_ROWS["000001.SZ"])
        assert cr.row_key == "000001.SZ"
        assert cr.row_sha256 == row_sha256(FULL_ROWS["000001.SZ"])
        assert cr.snapshot_id == SNAP


class TestReconstruct:
    def test_reconstruct_yields_exact_consumed_subset(self) -> None:
        m = _manifest()
        resolved = m.reconstruct_consumed({SNAP: FULL_ROWS})
        # Precise subset — not the wider 5-row snapshot.
        assert len(resolved) == 2 < len(FULL_ROWS)
        keys = {r.row_key for r in resolved}
        assert keys == set(CONSUMED_KEYS)
        for r in resolved:
            assert r.row_bytes == FULL_ROWS[r.row_key]

    def test_drift_detected_when_row_bytes_change(self) -> None:
        m = _manifest()
        tampered = dict(FULL_ROWS)
        tampered["000001.SZ"] = b"000001.SZ,99.99"  # restated/drifted
        with pytest.raises(SignalInputError):
            m.reconstruct_consumed({SNAP: tampered})

    def test_missing_consumed_row_raises(self) -> None:
        m = _manifest()
        partial = {k: v for k, v in FULL_ROWS.items() if k != "300750.SZ"}
        with pytest.raises(SignalInputError):
            m.reconstruct_consumed({SNAP: partial})

    def test_unknown_snapshot_raises(self) -> None:
        m = _manifest()
        with pytest.raises(SignalInputError):
            m.reconstruct_consumed({uuid4(): FULL_ROWS})


class TestModel:
    def test_frozen(self) -> None:
        m = _manifest()
        with pytest.raises(Exception):
            m.signal_id = "x"  # type: ignore[misc]

    def test_consumed_row_frozen(self) -> None:
        cr = build_consumed_row(SNAP, "000001.SZ", FULL_ROWS["000001.SZ"])
        with pytest.raises(Exception):
            cr.row_key = "y"  # type: ignore[misc]


class TestStore:
    def test_put_get_by_signal_id(self, tmp_path: Path) -> None:
        store = SignalInputManifestStore(root=tmp_path)
        m = _manifest("SIG-A")
        store.put(m)
        loaded = store.get("SIG-A")
        assert loaded is not None
        assert loaded.feature_code_version == "alpha158-subset@v1"
        assert [c.row_key for c in loaded.consumed_rows] == list(CONSUMED_KEYS)
        # Reconstruct still works after a storage roundtrip.
        resolved = loaded.reconstruct_consumed({SNAP: FULL_ROWS})
        assert {r.row_key for r in resolved} == set(CONSUMED_KEYS)

    def test_duplicate_signal_id_rejected(self, tmp_path: Path) -> None:
        store = SignalInputManifestStore(root=tmp_path)
        store.put(_manifest("SIG-DUP"))
        with pytest.raises(SignalInputError):
            store.put(_manifest("SIG-DUP"))

    def test_reopened_store_reads_offline(self, tmp_path: Path) -> None:
        SignalInputManifestStore(root=tmp_path).put(_manifest("SIG-B"))
        assert SignalInputManifestStore(root=tmp_path).get("SIG-B") is not None

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        assert SignalInputManifestStore(root=tmp_path).get("nope") is None
