"""K-004 — adjustment factor artifact pin (not a policy string).

Red line A.4 (R0 §3): a policy string ("qfq") is insufficient to rebuild
adjusted OHLCV. Pin the factor table bytes + corporate-action raw rows +
algorithm version + numeric precision + rounding. A dividend/split
correction changes the adjusted features but is a NEW append-only
version — the old version still reconstructs bit-exact. Policy is chosen
per use: factor/backtest -> qfq; affordability/order price -> raw.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backend.marketdata_snapshot.adjust import (
    AdjustFactorArtifact,
    AdjustFactorStore,
    AdjustPolicy,
    AdjustUse,
    policy_for_use,
)

# factor table: a 2:1 event lands on 20260522 (factor jumps 1.0 -> 2.0).
FACTORS_V1 = (("20260520", "1.0"), ("20260521", "1.0"), ("20260522", "2.0"))
RAW_FACTOR_BYTES = b"trade_date,adj_factor\n20260520,1.0\n20260521,1.0\n20260522,2.0\n"


def _artifact(
    ts_code: str = "600519.SH",
    factors: tuple[tuple[str, str], ...] = FACTORS_V1,
    raw: bytes = RAW_FACTOR_BYTES,
    version: int = 1,
) -> AdjustFactorArtifact:
    return AdjustFactorArtifact.create(
        ts_code=ts_code,
        factors=factors,
        raw_factor_payload=raw,
        algorithm_version="tushare-adj@v1",
        price_precision=2,
        rounding="ROUND_HALF_UP",
        version=version,
    )


class TestPolicyPerUse:
    def test_factor_and_backtest_use_qfq(self) -> None:
        assert policy_for_use(AdjustUse.FACTOR) is AdjustPolicy.QFQ
        assert policy_for_use(AdjustUse.BACKTEST) is AdjustPolicy.QFQ

    def test_affordability_and_order_price_use_raw(self) -> None:
        assert policy_for_use(AdjustUse.AFFORDABILITY) is AdjustPolicy.RAW
        assert policy_for_use(AdjustUse.ORDER_PRICE) is AdjustPolicy.RAW


class TestArtifactModel:
    def test_create_computes_sha256(self) -> None:
        a = _artifact()
        import hashlib

        assert a.raw_factor_sha256 == hashlib.sha256(RAW_FACTOR_BYTES).hexdigest()
        assert a.raw_factor_payload == RAW_FACTOR_BYTES

    def test_sha256_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            AdjustFactorArtifact(
                ts_code="600519.SH",
                factors=FACTORS_V1,
                raw_factor_payload=RAW_FACTOR_BYTES,
                raw_factor_sha256="0" * 64,
                algorithm_version="x",
                price_precision=2,
                rounding="ROUND_HALF_UP",
            )

    def test_frozen(self) -> None:
        a = _artifact()
        with pytest.raises(Exception):
            a.price_precision = 4  # type: ignore[misc]


class TestBitExactReconstruction:
    def test_qfq_forward_adjusted(self) -> None:
        a = _artifact()
        # latest factor = 2.0; qfq(0520) = 10.00 * 1.0 / 2.0 = 5.00
        assert a.adjusted_close("20260520", Decimal("10.00"), AdjustPolicy.QFQ) == (
            Decimal("5.00")
        )
        # qfq on the latest date is unchanged (factor/latest == 1)
        assert a.adjusted_close("20260522", Decimal("20.00"), AdjustPolicy.QFQ) == (
            Decimal("20.00")
        )

    def test_hfq_backward_adjusted(self) -> None:
        a = _artifact()
        assert a.adjusted_close("20260522", Decimal("20.00"), AdjustPolicy.HFQ) == (
            Decimal("40.00")  # 20.00 * 2.0
        )

    def test_raw_unadjusted_for_order_price(self) -> None:
        a = _artifact()
        assert a.adjusted_close("20260520", Decimal("10.00"), AdjustPolicy.RAW) == (
            Decimal("10.00")
        )

    def test_rounding_half_up_applied_at_precision(self) -> None:
        a = _artifact()
        # 10.01 * 1.0 / 2.0 = 5.005 -> round half up @ 2dp -> 5.01
        assert a.adjusted_close("20260520", Decimal("10.01"), AdjustPolicy.QFQ) == (
            Decimal("5.01")
        )

    def test_unknown_date_raises(self) -> None:
        a = _artifact()
        with pytest.raises(KeyError):
            a.adjusted_close("20990101", Decimal("1.00"), AdjustPolicy.QFQ)


class TestRestatementKeepsOldReconstruction:
    def test_new_version_does_not_break_old(self, tmp_path: Path) -> None:
        store = AdjustFactorStore(root=tmp_path)
        v1 = _artifact(version=1)
        store.put(v1)
        # A later corporate action adds 20260523 (factor 3.0) — latest moves.
        factors_v2 = FACTORS_V1 + (("20260523", "3.0"),)
        raw_v2 = RAW_FACTOR_BYTES + b"20260523,3.0\n"
        v2 = _artifact(factors=factors_v2, raw=raw_v2, version=2)
        store.put(v2)

        # v1 still reconstructs against latest=2.0 -> qfq(0520)=5.00
        assert store.versions(ts_code="600519.SH")[0].adjusted_close(
            "20260520", Decimal("10.00"), AdjustPolicy.QFQ
        ) == Decimal("5.00")
        # v2 reconstructs against latest=3.0 -> qfq(0520)=3.33
        assert store.latest(ts_code="600519.SH").adjusted_close(
            "20260520", Decimal("10.00"), AdjustPolicy.QFQ
        ) == Decimal("3.33")
        # old raw factor bytes untouched
        assert store.versions(ts_code="600519.SH")[0].raw_factor_payload == (
            RAW_FACTOR_BYTES
        )


class TestStore:
    def test_put_get_roundtrip(self, tmp_path: Path) -> None:
        store = AdjustFactorStore(root=tmp_path)
        store.put(_artifact())
        loaded = store.latest(ts_code="600519.SH")
        assert loaded is not None
        assert loaded.algorithm_version == "tushare-adj@v1"
        assert loaded.raw_factor_payload == RAW_FACTOR_BYTES

    def test_checksum_verified_on_read(self, tmp_path: Path) -> None:
        """A tampered persisted payload fails the sha256 on read."""
        store = AdjustFactorStore(root=tmp_path)
        store.put(_artifact())
        # Corrupt the persisted jsonl payload field.
        jsonl = tmp_path / "adjust_factors.jsonl"
        text = jsonl.read_text(encoding="utf-8")
        jsonl.write_text(text.replace("dHJhZGVf", "XXXXXXXX"), encoding="utf-8")
        with pytest.raises(Exception):
            store.latest(ts_code="600519.SH")

    def test_reopened_store_offline(self, tmp_path: Path) -> None:
        AdjustFactorStore(root=tmp_path).put(_artifact())
        assert AdjustFactorStore(root=tmp_path).latest(ts_code="600519.SH") is not None

    def test_dup_version_rejected(self, tmp_path: Path) -> None:
        store = AdjustFactorStore(root=tmp_path)
        store.put(_artifact(version=1))
        with pytest.raises(Exception):
            store.put(_artifact(version=1))
