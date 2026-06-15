"""PIT same-source export for the rqalpha oracle (Option B, AE-002).

The differential oracle is only meaningful if rqalpha sees the **same** bars the
live MockBroker shadow saw (R-002-amendment-2026-06-14 §2.2 / R0 §3): not 米筐's
bundle (a different source — a divergence would then be "different data", not an
execution-logic bug). So the main env reads the K-002 byte-exact PIT snapshots,
reconstructs the forward-adjusted (qfq) bars *as of* the window end, and writes a
content-addressed export into the subprocess workdir:

* ``bars.csv`` — byte-stable CSV of qfq OHLC + volume (its sha256 is pinned into
  ``spec.json`` and re-checked by the venv entry; pyarrow/parquet is absent from
  both envs and installing it would move the main-env baseline, so a canonical
  CSV — content-addressed + checksum, exactly the R0 §3 red line — is used);
* ``spec.json`` — dates, capital, friction (from ``config/broker.yaml``), the
  per-instrument metadata + the deterministic order schedule;
* ``manifest.json`` — the bars sha256 + adjustment pin so the run is replayable.

This module lives in ``backend/backtest`` (the ``[BACKTEST]`` import allowlist
permits ``backend.data`` + ``backend.marketdata_snapshot`` — it forbids only
llm/agents/api/broker), so it can read the PIT store + the survivorship board
classifier directly. The runner (in ``strategy_evolution``, which cannot import
``backend.data``) consumes it through the injected :class:`PitExporter` Protocol.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

# ruff: noqa: TID251 — the [BACKTEST] redline (P2-2-amendment-2026-06-14) allows
# backend.data for the PIT exporter; only the global ban needs a local waiver.
from backend.backtest import rqalpha_protocol as proto
from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import (
    canonical_csv_bytes,
    parse_csv_bytes,
)
from backend.data.stock_metadata import Board, classify_board
from backend.marketdata_snapshot.store import SnapshotStore

_QFQ_PRECISION = Decimal("0.0001")
"""qfq prices quantised to 4dp — deterministic str form for content-addressing."""

# Wide synthetic limits so a close-priced replay fill is never spuriously
# blocked; real PIT price-limit (stk_limit) enforcement at-fill is AE-004.
_LIMIT_UP_MULT = Decimal("1.21")
_LIMIT_DOWN_MULT = Decimal("0.79")

_SZ_TRANSFER_FEE_BOARDS = frozenset({Board.SZ_MAIN, Board.CHUANGYE})


class PitExportError(RuntimeError):
    """Raised when the same-source export cannot be produced (fail-closed)."""


@dataclass(frozen=True)
class BrokerFriction:
    """Friction inputs mirroring the MockBroker cost model (single source).

    ``commission_rate`` / ``min_commission`` / ``stamp_tax_rate`` /
    ``slippage_bps_by_board`` come from ``config/broker.yaml``;
    ``transfer_fee_rate`` is the ``backend.broker.cost_calculator``
    ``TRANSFER_FEE_RATE_SZ`` constant (0.0000341, the SZ 过户费 rate, not a yaml
    key). Passed verbatim into ``spec.json``; the venv entry re-implements the
    broker formulas over these numbers (it cannot import ``backend.broker``).
    The production wiring must source all five from those authorities — never
    hand-typed — so the oracle and the MockBroker charge identical friction.
    """

    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    transfer_fee_rate: float
    slippage_bps_by_board: Mapping[str, float]


@dataclass(frozen=True)
class ExportManifest:
    """What the export produced — returned to the runner, pinned for replay."""

    bars_sha256: str
    instruments: tuple[str, ...]
    trading_days: int
    asof_date: str


def _ts_to_order_book_id(ts_code: str) -> str:
    """``600519.SH`` -> ``600519.XSHG``; ``000001.SZ`` -> ``000001.XSHE``."""
    code, _, exch = ts_code.partition(".")
    if exch == "SH":
        return f"{code}.XSHG"
    if exch == "SZ":
        return f"{code}.XSHE"
    raise PitExportError(f"unsupported ts_code exchange suffix: {ts_code!r}")


def _transfer_fee_applies(code6: str, board: Board) -> bool:
    """Mirror ``cost_calculator``: SZ_MAIN / CHUANGYE / 159 ETF carry the fee."""
    return board in _SZ_TRANSFER_FEE_BOARDS or code6.startswith("159")


class SnapshotPitExporter:
    """Export qfq PIT bars + spec into the subprocess workdir (Option B).

    Args:
        snapshot_store: the K-002 byte-exact PIT store (verify-before-adopt).
        friction: friction numbers from ``config/broker.yaml``.
        calendar: trade dates (YYYYMMDD) the window may contain — filtered to
            ``[spec.start_date, spec.end_date]`` and intersected with the days a
            ``daily`` snapshot actually exists for.
    """

    def __init__(
        self,
        *,
        snapshot_store: SnapshotStore,
        friction: BrokerFriction,
        calendar: Sequence[str],
    ) -> None:
        self._store = snapshot_store
        self._friction = friction
        self._calendar = sorted(calendar)

    def export(self, spec: object, workdir: Path) -> ExportManifest:
        """Write ``spec.json`` + ``bars.csv`` into ``workdir``; return manifest.

        ``spec`` is a ``BacktestSpec`` (duck-typed: ``strategy_hash`` /
        ``strategy_source_path`` / ``start_date`` / ``end_date`` /
        ``initial_capital`` as YYYYMMDD strings + float). Raises
        :class:`PitExportError` on any data gap (fail-closed — the runner maps it
        to ORACLE_UNAVAILABLE, never a silent pass).
        """
        strategy_hash = getattr(spec, "strategy_hash")
        source_path = Path(getattr(spec, "strategy_source_path"))
        start = _yyyymmdd(getattr(spec, "start_date"))
        end = _yyyymmdd(getattr(spec, "end_date"))
        capital = float(getattr(spec, "initial_capital"))

        orders = self._load_orders(source_path, strategy_hash)
        ts_codes = sorted({str(o["ts_code"]) for o in orders})
        if not ts_codes:
            raise PitExportError("strategy artifact has no orders")

        days = [d for d in self._calendar if start <= d <= end]
        if not days:
            raise PitExportError(
                f"no calendar trade dates in [{start}, {end}]"
            )

        bars_rows, instruments = self._build_bars(ts_codes, days, asof=end)
        bars_df = pd.DataFrame(bars_rows, columns=list(proto.BARS_COLUMNS))
        bars_bytes = canonical_csv_bytes(bars_df)
        bars_sha256 = hashlib.sha256(bars_bytes).hexdigest()

        order_specs = [
            {
                proto.ORD_TRADE_DATE: str(o["trade_date"]),
                proto.ORD_ORDER_BOOK_ID: _ts_to_order_book_id(str(o["ts_code"])),
                proto.ORD_SIDE: str(o["side"]),
                proto.ORD_SHARES: int(o["shares"]),
            }
            for o in orders
        ]
        spec_json = {
            proto.SPEC_SCHEMA_VERSION: proto.SCHEMA_VERSION,
            proto.SPEC_START_DATE: start,
            proto.SPEC_END_DATE: end,
            proto.SPEC_INITIAL_CAPITAL: capital,
            proto.SPEC_STRATEGY_HASH: strategy_hash,
            proto.SPEC_BARS_SHA256: bars_sha256,
            proto.SPEC_INSTRUMENTS: instruments,
            proto.SPEC_ORDERS: order_specs,
            proto.SPEC_FRICTION: {
                proto.FRIC_COMMISSION_RATE: self._friction.commission_rate,
                proto.FRIC_MIN_COMMISSION: self._friction.min_commission,
                proto.FRIC_STAMP_TAX_RATE: self._friction.stamp_tax_rate,
                proto.FRIC_TRANSFER_FEE_RATE: self._friction.transfer_fee_rate,
                proto.FRIC_SLIPPAGE_BPS_BY_BOARD: dict(
                    self._friction.slippage_bps_by_board
                ),
            },
        }

        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / proto.BARS_FILENAME).write_bytes(bars_bytes)
        (workdir / proto.SPEC_FILENAME).write_text(
            json.dumps(spec_json, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
        (workdir / "manifest.json").write_text(
            json.dumps(
                {
                    "bars_sha256": bars_sha256,
                    "asof_date": end,
                    "vendor": VENDOR,
                    "instruments": [i[proto.INS_ORDER_BOOK_ID] for i in instruments],
                    "trading_days": len(days),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return ExportManifest(
            bars_sha256=bars_sha256,
            instruments=tuple(str(i[proto.INS_ORDER_BOOK_ID]) for i in instruments),
            trading_days=len(days),
            asof_date=end,
        )

    # -- internal ------------------------------------------------------
    def _load_orders(
        self, source_path: Path, strategy_hash: str
    ) -> list[dict[str, Any]]:
        """Read + hash-verify the deterministic order schedule artifact."""
        if not source_path.exists():
            raise PitExportError(f"strategy artifact missing: {source_path}")
        raw = source_path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != strategy_hash:
            raise PitExportError(
                f"strategy artifact sha256 {actual[:12]} != spec "
                f"{strategy_hash[:12]} — refusing to export a different strategy"
            )
        doc = json.loads(raw.decode("utf-8"))
        orders = doc.get("orders")
        if not isinstance(orders, list):
            raise PitExportError("strategy artifact has no 'orders' list")
        return orders

    def _build_bars(
        self, ts_codes: list[str], days: list[str], *, asof: str
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        rows: list[dict[str, object]] = []
        instruments: list[dict[str, object]] = []
        for ts_code in ts_codes:
            code6 = ts_code.partition(".")[0]
            board = classify_board(code6)
            obid = _ts_to_order_book_id(ts_code)
            qfq = self._qfq_bars(ts_code, days, asof=asof)
            if not qfq:
                raise PitExportError(
                    f"no PIT daily/adj_factor bars for {ts_code} in window"
                )
            for day, bar in qfq.items():
                rows.append(
                    {
                        proto.INS_ORDER_BOOK_ID: obid,
                        "trade_date": int(day),
                        "open": str(bar["open"]),
                        "high": str(bar["high"]),
                        "low": str(bar["low"]),
                        "close": str(bar["close"]),
                        "volume": str(bar["volume"]),
                        "total_turnover": str(bar["amount"]),
                        "limit_up": str(
                            (bar["close"] * _LIMIT_UP_MULT).quantize(_QFQ_PRECISION)
                        ),
                        "limit_down": str(
                            (bar["close"] * _LIMIT_DOWN_MULT).quantize(_QFQ_PRECISION)
                        ),
                    }
                )
            instruments.append(
                {
                    proto.INS_ORDER_BOOK_ID: obid,
                    proto.INS_TYPE: "ETF" if board is Board.ETF else "CS",
                    proto.INS_BOARD: board.value,
                    proto.INS_LISTED_DATE: "2000-01-01",
                    proto.INS_DE_LISTED_DATE: "2999-12-31",
                    proto.INS_ROUND_LOT: 100,
                    proto.INS_TRANSFER_FEE_APPLIES: _transfer_fee_applies(
                        code6, board
                    ),
                }
            )
        return rows, instruments

    def _qfq_bars(
        self, ts_code: str, days: list[str], *, asof: str
    ) -> dict[str, dict[str, Decimal]]:
        """Forward-adjusted (qfq) OHLC for ``ts_code`` over ``days`` as of ``asof``.

        ``qfq(d) = raw(d) * factor(d) / factor(asof)`` — only factors on/before
        ``asof`` are used (a later split never leaks backward; R0 §3).
        """
        raw: dict[str, dict[str, Decimal]] = {}
        factor: dict[str, Decimal] = {}
        for day in days:
            if day > asof:
                continue
            daily = self._store.latest(
                vendor=VENDOR, endpoint="daily", trade_date=day
            )
            adj = self._store.latest(
                vendor=VENDOR, endpoint="adj_factor", trade_date=day
            )
            if daily is None or adj is None:
                continue
            ddf = parse_csv_bytes(daily.raw_payload)
            adf = parse_csv_bytes(adj.raw_payload)
            drow = ddf[ddf["ts_code"].astype(str) == ts_code]
            arow = adf[adf["ts_code"].astype(str) == ts_code]
            if drow.empty or arow.empty:
                continue
            r = drow.iloc[0]
            raw[day] = {
                "open": _dec(r["open"]),
                "high": _dec(r["high"]),
                "low": _dec(r["low"]),
                "close": _dec(r["close"]),
                "volume": _dec(r.get("vol", 0)),
                "amount": _dec(r.get("amount", 0)),
            }
            factor[day] = _dec(arow.iloc[0]["adj_factor"])
        if not factor:
            return {}
        asof_factor = factor[max(factor)]
        out: dict[str, dict[str, Decimal]] = {}
        for day, bar in raw.items():
            mult = factor[day] / asof_factor
            out[day] = {
                "open": (bar["open"] * mult).quantize(_QFQ_PRECISION),
                "high": (bar["high"] * mult).quantize(_QFQ_PRECISION),
                "low": (bar["low"] * mult).quantize(_QFQ_PRECISION),
                "close": (bar["close"] * mult).quantize(_QFQ_PRECISION),
                "volume": bar["volume"],
                "amount": bar["amount"],
            }
        return out


def _dec(value: object) -> Decimal:
    return Decimal(str(value).strip())


def _yyyymmdd(value: str) -> str:
    """Accept ``YYYYMMDD`` or ``YYYY-MM-DD``; return ``YYYYMMDD``."""
    s = str(value).replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise PitExportError(f"bad date {value!r} (want YYYYMMDD)")
    return s


__all__ = [
    "BrokerFriction",
    "ExportManifest",
    "PitExportError",
    "SnapshotPitExporter",
]
