"""K-005 — offline bit-exact replay of a signal's feature input.

Red line A.5 (R0 §3): replay <signal_id> must, with **no network**,
rebuild the exact feature input fed to that signal from the stored raw
bytes + pinned config hashes. Backtest / shadow / live all read by
snapshot_id through this single entry, so a P0-6 acceptance gate is
validating signal — not noise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.marketdata_snapshot.replay import (
    CsvRowParser,
    Replayer,
    ReplayError,
)
from backend.marketdata_snapshot.signal_input_manifest import (
    SignalInputManifest,
    SignalInputManifestStore,
    build_consumed_row,
)
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore

CSV = (
    b"ts_code,close\n"
    b"000001.SZ,12.34\n"
    b"600519.SH,1680.0\n"
    b"300750.SZ,210.5\n"
    b"000002.SZ,9.87\n"
    b"601318.SH,45.6\n"
)
CONSUMED_KEYS = ("000001.SZ", "300750.SZ")


def _seed(tmp_path: Path, *, signal_id: str = "SIG-1") -> tuple[
    SnapshotStore, SignalInputManifestStore, MarketDataSnapshot
]:
    snap_store = SnapshotStore(root=tmp_path)
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint="daily",
        params={"trade_date": "20260522"},
        trade_date="20260522",
        raw_payload=CSV,
        encoding="csv",
        compression="none",
        fetch_time_utc=datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
    )
    snap_store.put(snap)

    # Build the manifest using the SAME parser the replay uses, so the
    # consumed-row hashes line up with what replay re-derives.
    rows = CsvRowParser().parse(snap)
    consumed = tuple(
        build_consumed_row(snap.snapshot_id, k, rows[k]) for k in CONSUMED_KEYS
    )
    man_store = SignalInputManifestStore(root=tmp_path)
    man_store.put(
        SignalInputManifest(
            signal_id=signal_id,
            created_at=datetime(2026, 5, 22, 9, 31, tzinfo=UTC),
            snapshot_ids=(snap.snapshot_id,),
            consumed_rows=consumed,
            feature_code_version="alpha158-subset@v1",
            config_hashes={"screening": "abc"},
            join_filter_params={},
        )
    )
    return snap_store, man_store, snap


class TestCsvRowParser:
    def test_parses_data_rows_keyed_by_first_column_skipping_header(self) -> None:
        snap = MarketDataSnapshot.create(
            vendor="tushare",
            endpoint="daily",
            params={"trade_date": "20260522"},
            trade_date="20260522",
            raw_payload=CSV,
            encoding="csv",
            compression="none",
            fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
        )
        rows = CsvRowParser().parse(snap)
        assert set(rows) == {
            "000001.SZ",
            "600519.SH",
            "300750.SZ",
            "000002.SZ",
            "601318.SH",
        }
        assert rows["000001.SZ"] == b"000001.SZ,12.34"


class TestReplay:
    def test_replay_returns_exact_consumed_subset(self, tmp_path: Path) -> None:
        snap_store, man_store, _ = _seed(tmp_path)
        result = Replayer(snap_store, man_store).replay("SIG-1")
        assert {r.row_key for r in result.consumed} == set(CONSUMED_KEYS)
        assert len(result.consumed) == 2  # subset of the 5-row snapshot
        assert result.feature_code_version == "alpha158-subset@v1"
        assert len(result.feature_input_digest) == 64

    def test_replay_is_deterministic_bit_exact(self, tmp_path: Path) -> None:
        snap_store, man_store, _ = _seed(tmp_path)
        replayer = Replayer(snap_store, man_store)
        d1 = replayer.replay("SIG-1").feature_input_digest
        d2 = replayer.replay("SIG-1").feature_input_digest
        assert d1 == d2

    def test_replay_offline_after_reopen(self, tmp_path: Path) -> None:
        """A fresh process (new store instances, no network) replays
        identically from disk."""
        snap_store, man_store, _ = _seed(tmp_path)
        d1 = Replayer(snap_store, man_store).replay("SIG-1").feature_input_digest
        d2 = (
            Replayer(SnapshotStore(tmp_path), SignalInputManifestStore(tmp_path))
            .replay("SIG-1")
            .feature_input_digest
        )
        assert d1 == d2

    def test_unknown_signal_raises(self, tmp_path: Path) -> None:
        snap_store, man_store, _ = _seed(tmp_path)
        with pytest.raises(ReplayError):
            Replayer(snap_store, man_store).replay("SIG-UNKNOWN")

    def test_tampered_snapshot_fails_closed(self, tmp_path: Path) -> None:
        snap_store, man_store, _ = _seed(tmp_path)
        # Corrupt the stored payload bytes -> snapshot checksum fails.
        pf = list((tmp_path / "payloads").rglob("*.bin"))[0]
        pf.write_bytes(b"tampered")
        with pytest.raises(Exception):
            Replayer(snap_store, man_store).replay("SIG-1")

    def test_three_consumers_share_one_snapshot_entry(self, tmp_path: Path) -> None:
        """Backtest / shadow / live all read the same snapshot_id via the
        same Replayer entry — identical digest proves single source."""
        snap_store, man_store, snap = _seed(tmp_path)
        result = Replayer(snap_store, man_store).replay("SIG-1")
        assert result.snapshot_ids == (snap.snapshot_id,)
        # Each "consumer" is just another replay over the same entry.
        digests = {
            Replayer(snap_store, man_store).replay("SIG-1").feature_input_digest
            for _ in range(3)
        }
        assert len(digests) == 1


class TestCli:
    def test_cli_replays_and_prints_digest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed(tmp_path)
        from scripts.replay_signal import main

        rc = main(["SIG-1", "--root", str(tmp_path), "--show-rows"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "feature_input_digest:" in out
        assert "000001.SZ" in out

    def test_cli_unknown_signal_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed(tmp_path)
        from scripts.replay_signal import main

        rc = main(["SIG-NOPE", "--root", str(tmp_path)])
        assert rc == 2

    def test_cli_tampered_snapshot_fail_closed_nonzero(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        list((tmp_path / "payloads").rglob("*.bin"))[0].write_bytes(b"x")
        from scripts.replay_signal import main

        assert main(["SIG-1", "--root", str(tmp_path)]) == 3
