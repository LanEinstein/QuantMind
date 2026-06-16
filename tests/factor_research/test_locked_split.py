"""Tests for the sacred train/val/test split loader + tamper-evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.factor_research.locked_split import (
    LockedSplit,
    LockVerificationError,
    SacredTestAccessError,
)


def _sha(dates: list[str]) -> str:
    return hashlib.sha256("|".join(dates).encode("utf-8")).hexdigest()


def _calendar() -> tuple[str, ...]:
    # 10 sequential trading days
    return tuple(f"202001{d:02d}" for d in range(1, 11))


def _lock(cal: tuple[str, ...], *, test=3, embargo=2) -> dict:
    test_d = list(cal[len(cal) - test :])
    emb_d = list(cal[len(cal) - test - embargo : len(cal) - test])
    tv_d = list(cal[: len(cal) - test - embargo])

    def block(d: list[str]) -> dict:
        return {"n_days": len(d), "start": d[0], "end": d[-1], "dates_sha256": _sha(d)}

    return {"train_val": block(tv_d), "embargo": block(emb_d), "test": block(test_d)}


def test_from_lock_happy_path() -> None:
    cal = _calendar()
    split = LockedSplit.from_lock(_lock(cal), cal)
    assert split.train_val_dates == cal[:5]
    assert split.embargo_dates == cal[5:7]
    assert split.test_dates == cal[7:]


def test_assert_not_test_guards_test_dates() -> None:
    cal = _calendar()
    split = LockedSplit.from_lock(_lock(cal), cal)
    split.assert_not_test("20200101")  # train_val: ok
    split.assert_not_test("20200106")  # embargo: ok (not test)
    assert split.is_test("20200110") is True
    with pytest.raises(SacredTestAccessError, match="SACRED"):
        split.assert_not_test("20200110")  # test: blocked
    with pytest.raises(SacredTestAccessError):
        split.assert_all_not_test(["20200101", "20200109"])  # one test date


def test_hash_mismatch_fails_closed() -> None:
    cal = _calendar()
    lock = _lock(cal)
    lock["test"]["dates_sha256"] = "deadbeef" * 8  # tampered
    with pytest.raises(LockVerificationError, match="dates_sha256 mismatch"):
        LockedSplit.from_lock(lock, cal)


def test_calendar_drift_inside_window_fails_closed() -> None:
    # The realistic drift: a re-ingest fills a previously-missing day INSIDE a
    # locked window (cf. the 20200212 hole). Removing an interior test day
    # shrinks the start..end span -> n_days/hash mismatch -> fail closed.
    cal = _calendar()
    lock = _lock(cal)  # test = 20200108..20200110 (3 days)
    drifted = tuple(d for d in cal if d != "20200109")  # interior test day gone
    with pytest.raises(LockVerificationError):
        LockedSplit.from_lock(lock, drifted)


def test_n_days_mismatch_fails_closed() -> None:
    cal = _calendar()
    lock = _lock(cal)
    lock["test"]["n_days"] = 99
    with pytest.raises(LockVerificationError, match="window has"):
        LockedSplit.from_lock(lock, cal)


@pytest.mark.skipif(
    not Path("config/research/test_set_lock.json").exists()
    or not Path("data/marketdata_pit/index.jsonl").exists(),
    reason="requires the committed lock + locally-ingested PIT calendar",
)
def test_real_lock_loads_and_verifies() -> None:
    split = LockedSplit.load()
    assert len(split.train_val_dates) == 2509
    assert len(split.embargo_dates) == 20
    assert len(split.test_dates) == 250
    assert split.test_dates[0] == "20250604"
    assert split.test_dates[-1] == "20260612"
    assert split.is_test("20260612") is True
    assert split.is_test("20150105") is False
    # contiguity: train_val ends strictly before test starts
    assert split.train_val_dates[-1] == "20250430"
