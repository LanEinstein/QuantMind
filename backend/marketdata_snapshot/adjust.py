"""Adjustment-factor artifact pin — bit-exact OHLCV reconstruction (K-004).

Red line A.4 (R0 §3): a policy string (``"qfq"``) is **not enough** to
rebuild adjusted prices. We pin the factor table bytes + algorithm
version + numeric precision + rounding rule, so qfq/hfq series can be
rebuilt **bit-exact** from the artifact + raw rows. A dividend/split
correction changes the adjusted features but is a **new append-only
version** — the old version still reconstructs its original series.

Policy is chosen **per use**, not globally:

* factors / backtest -> ``qfq`` (forward-adjusted, continuous series)
* affordability / order price -> ``raw`` (the actual tradeable price)

All arithmetic uses :class:`decimal.Decimal` with an explicit quantize
so the output does not depend on float representation or platform.
"""

from __future__ import annotations

import base64
import hashlib
from decimal import Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.marketdata_snapshot._jsonl import append_row, load_rows

log = structlog.get_logger(component="marketdata_snapshot.adjust")

ADJUST_FACTOR_ARTIFACT_SCHEMA_VERSION = 1


class AdjustPolicy(StrEnum):
    """How a price is adjusted."""

    QFQ = "qfq"  # forward-adjusted — factors / backtest
    HFQ = "hfq"  # backward-adjusted
    RAW = "raw"  # unadjusted — affordability / order price


class AdjustUse(StrEnum):
    """The consumer's intent, which fixes the policy."""

    FACTOR = "factor"
    BACKTEST = "backtest"
    AFFORDABILITY = "affordability"
    ORDER_PRICE = "order_price"


_USE_POLICY: dict[AdjustUse, AdjustPolicy] = {
    AdjustUse.FACTOR: AdjustPolicy.QFQ,
    AdjustUse.BACKTEST: AdjustPolicy.QFQ,
    AdjustUse.AFFORDABILITY: AdjustPolicy.RAW,
    AdjustUse.ORDER_PRICE: AdjustPolicy.RAW,
}


def policy_for_use(use: AdjustUse) -> AdjustPolicy:
    """Return the adjustment policy mandated for a given use."""
    return _USE_POLICY[use]


class AdjustFactorArtifact(BaseModel):
    """A pinned per-stock adjustment-factor table + reconstruction rules.

    ``factors`` is a tuple of ``(trade_date, adj_factor)`` with the factor
    stored as a **string** so the Decimal value is exact. ``raw_factor_
    payload`` pins the original vendor bytes (red line A.4 — store bytes),
    guarded by ``raw_factor_sha256``. ``version`` increments for corporate
    -action restatements; old versions keep their own bytes + factors.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(
        default=ADJUST_FACTOR_ARTIFACT_SCHEMA_VERSION, ge=1
    )
    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    factors: tuple[tuple[str, str], ...]
    """``((trade_date, adj_factor_str), …)`` — factor as a Decimal string."""
    raw_factor_payload: bytes
    raw_factor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str = Field(min_length=1)
    """e.g. ``tushare-adj@v1`` — the pinned adjustment algorithm."""
    price_precision: int = Field(ge=0, le=8)
    """Decimal places the reconstructed price is quantized to."""
    rounding: str = Field(min_length=1)
    """A :mod:`decimal` rounding mode name, e.g. ``ROUND_HALF_UP``."""
    corporate_action_rows: tuple[str, ...] = Field(default_factory=tuple)
    """Raw dividend/split rows pinned for provenance (optional)."""
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check_payload_integrity(self) -> AdjustFactorArtifact:
        digest = hashlib.sha256(self.raw_factor_payload).hexdigest()
        if self.raw_factor_sha256 != digest:
            raise ValueError(
                "raw_factor_sha256 mismatch: stored "
                f"{self.raw_factor_sha256} != computed {digest}"
            )
        return self

    @model_validator(mode="after")
    def _check_factors_non_empty(self) -> AdjustFactorArtifact:
        if not self.factors:
            raise ValueError("factors must contain at least one row")
        return self

    # -- factor lookup -------------------------------------------------

    def _factor_map(self) -> dict[str, Decimal]:
        return {d: Decimal(f) for d, f in self.factors}

    def _latest_factor(self) -> Decimal:
        """Factor of the most recent trade_date — the qfq reference."""
        latest_date = max(d for d, _ in self.factors)
        return Decimal(dict(self.factors)[latest_date])

    # -- reconstruction ------------------------------------------------

    def adjusted_close(
        self, trade_date: str, raw_close: Decimal, policy: AdjustPolicy
    ) -> Decimal:
        """Reconstruct the adjusted close for a date, bit-exact.

        Raises:
            KeyError: ``trade_date`` not in the pinned factor table.
        """
        if policy is AdjustPolicy.RAW:
            return self._quantize(raw_close)
        factor = self._factor_map()[trade_date]  # KeyError if unknown date
        if policy is AdjustPolicy.HFQ:
            value = raw_close * factor
        else:  # QFQ
            value = raw_close * factor / self._latest_factor()
        return self._quantize(value)

    def _quantize(self, value: Decimal) -> Decimal:
        quantum = Decimal(1).scaleb(-self.price_precision)
        with localcontext() as ctx:
            ctx.prec = 50  # ample headroom; rounding is at quantize time
            return value.quantize(quantum, rounding=self.rounding)

    # -- (de)serialisation ---------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        ts_code: str,
        factors: tuple[tuple[str, str], ...],
        raw_factor_payload: bytes,
        algorithm_version: str,
        price_precision: int,
        rounding: str,
        corporate_action_rows: tuple[str, ...] = (),
        version: int = 1,
    ) -> AdjustFactorArtifact:
        """Build an artifact, computing ``raw_factor_sha256``."""
        return cls(
            ts_code=ts_code,
            factors=factors,
            raw_factor_payload=raw_factor_payload,
            raw_factor_sha256=hashlib.sha256(raw_factor_payload).hexdigest(),
            algorithm_version=algorithm_version,
            price_precision=price_precision,
            rounding=rounding,
            corporate_action_rows=corporate_action_rows,
            version=version,
        )

    def to_row(self) -> dict[str, Any]:
        """Index row — payload base64-encoded (small per-stock table)."""
        return {
            "schema_version": self.schema_version,
            "ts_code": self.ts_code,
            "factors": [list(f) for f in self.factors],
            "raw_factor_b64": base64.b64encode(self.raw_factor_payload).decode(
                "ascii"
            ),
            "raw_factor_sha256": self.raw_factor_sha256,
            "algorithm_version": self.algorithm_version,
            "price_precision": self.price_precision,
            "rounding": self.rounding,
            "corporate_action_rows": list(self.corporate_action_rows),
            "version": self.version,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AdjustFactorArtifact:
        """Rebuild from a stored row, decoding + verifying the bytes."""
        payload = base64.b64decode(row["raw_factor_b64"])
        # construct via explicit fields (strict mode, native tuples); the
        # sha validator re-verifies the decoded bytes (fail-closed).
        return cls(
            schema_version=row["schema_version"],
            ts_code=row["ts_code"],
            factors=tuple((d, f) for d, f in row["factors"]),
            raw_factor_payload=payload,
            raw_factor_sha256=row["raw_factor_sha256"],
            algorithm_version=row["algorithm_version"],
            price_precision=row["price_precision"],
            rounding=row["rounding"],
            corporate_action_rows=tuple(row["corporate_action_rows"]),
            version=row["version"],
        )


class AdjustFactorStore:
    """Append-only JSONL store keyed by (ts_code, version)."""

    _FILE = "adjust_factors.jsonl"
    _LOCK = "adjust_factors.lock"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._path = self._root / self._FILE
        self._lock = FileLock(str(self._root / self._LOCK))
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, artifact: AdjustFactorArtifact) -> AdjustFactorArtifact:
        with self._lock:
            for row in load_rows(self._path):
                if (
                    row["ts_code"] == artifact.ts_code
                    and row["version"] == artifact.version
                ):
                    raise ValueError(
                        f"adjust factor artifact {artifact.ts_code} v"
                        f"{artifact.version} already stored — append-only"
                    )
            append_row(self._path, artifact.to_row(), self._lock)
        log.info(
            "adjust_factor_artifact_put",
            ts_code=artifact.ts_code,
            version=artifact.version,
            sha256=artifact.raw_factor_sha256[:12],
        )
        return artifact

    def versions(self, *, ts_code: str) -> tuple[AdjustFactorArtifact, ...]:
        """All versions for a ts_code, ordered by ``version`` ascending."""
        rows = [r for r in load_rows(self._path) if r["ts_code"] == ts_code]
        rows.sort(key=lambda r: r["version"])
        return tuple(AdjustFactorArtifact.from_row(r) for r in rows)

    def latest(self, *, ts_code: str) -> AdjustFactorArtifact | None:
        versions = self.versions(ts_code=ts_code)
        return versions[-1] if versions else None


__all__ = [
    "ADJUST_FACTOR_ARTIFACT_SCHEMA_VERSION",
    "AdjustFactorArtifact",
    "AdjustFactorStore",
    "AdjustPolicy",
    "AdjustUse",
    "policy_for_use",
]
