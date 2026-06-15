"""rqalpha subprocess entry point (AE-002) — ``python -m rqalpha_entry``.

Invoked by :class:`backend.strategy_evolution.backtest_oracle.RqalphaBacktestRunner`
in the isolated oracle venv with ``PYTHONPATH`` pointing at ``backend/backtest``
(so this resolves as the top-level ``rqalpha_entry`` package, importing **no**
``backend.*``). It:

1. reads ``spec.json`` + ``bars.csv`` from ``--workdir`` (the same-source PIT
   export the main env wrote — Option B);
2. builds the custom data source + friction tables + a deterministic
   order-replay strategy;
3. runs the rqalpha backtest;
4. writes ``result.json`` **atomically** (temp + ``os.replace``) plus a
   ``result.json.sha256`` sidecar, so a half-written file can never be adopted.

All stdout/stderr is rqalpha's logging; the *only* channel back to the main env
is the result file (stdout pollution cannot corrupt the JSON contract).

The spec / result JSON keys are the literals declared in
``backend.backtest.rqalpha_protocol`` (re-stated here because the venv cannot
import backend); the end-to-end integration test exercises the round trip.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rqalpha
from rqalpha import run_func
from rqalpha.api import order_shares
from rqalpha.model.instrument import Instrument
from rqalpha.utils.datetime_func import convert_date_to_int
from rqalpha_entry import friction, mod
from rqalpha_entry.data_source import BAR_DTYPE, PitExportDataSource

_SCHEMA_VERSION = 1
_BARS_FILENAME = "bars.csv"
_SPEC_FILENAME = "spec.json"
_RESULT_FILENAME = "result.json"
_RESULT_CHECKSUM_FILENAME = "result.json.sha256"


def _yyyymmdd_to_date(yyyymmdd: int) -> _dt.date:
    return _dt.date(yyyymmdd // 10_000, (yyyymmdd // 100) % 100, yyyymmdd % 100)


def _load_bars(bars_path: Path) -> dict[str, np.ndarray]:
    """Parse the byte-stable bars CSV into per-instrument structured arrays.

    ``trade_date`` is stored human-readable (YYYYMMDD) but rqalpha's bar
    ``datetime`` field is the ``convert_date_to_int`` form (YYYYMMDD*1e6), so
    convert on load — both the data source's lookups and rqalpha use that form.
    """
    df = pd.read_csv(StringIO(bars_path.read_text(encoding="utf-8")))
    out: dict[str, np.ndarray] = {}
    for obid, grp in df.groupby("order_book_id"):
        grp = grp.sort_values("trade_date")
        rows = [
            (
                np.uint64(convert_date_to_int(_yyyymmdd_to_date(int(r.trade_date)))),
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                float(r.volume),
                float(r.total_turnover),
                float(r.limit_up),
                float(r.limit_down),
            )
            for r in grp.itertuples(index=False)
        ]
        out[str(obid)] = np.array(rows, dtype=BAR_DTYPE)
    return out


def _build_instruments(specs: list[dict[str, Any]]) -> dict[str, Instrument]:
    instruments: dict[str, Instrument] = {}
    for s in specs:
        obid = s["order_book_id"]
        exchange = "XSHG" if obid.endswith(".XSHG") else "XSHE"
        instruments[obid] = Instrument(
            {
                "order_book_id": obid,
                "symbol": obid,
                "type": s["instrument_type"],
                "listed_date": s["listed_date"],
                "de_listed_date": s["de_listed_date"],
                "exchange": exchange,
                "round_lot": float(s.get("round_lot", 100)),
                "board_type": "MainBoard",
                "status": "Active",
                "market_tplus": 1,
            }
        )
    return instruments


def _trading_days(bars: dict[str, np.ndarray]) -> list[_dt.date]:
    ints: set[int] = set()
    for arr in bars.values():
        ints.update(int(v) for v in arr["datetime"])
    # datetime field is convert_date_to_int form (YYYYMMDD*1e6).
    return sorted(_yyyymmdd_to_date(i // 1_000_000) for i in ints)


def _env_fingerprint() -> dict[str, str]:
    blas = "unknown"
    try:  # numpy >= 1.26 exposes a structured config
        cfg = np.show_config(mode="dicts")  # type: ignore[call-overload]
        if isinstance(cfg, dict):
            deps = cfg.get("Build Dependencies", {})
            blas = str(deps.get("blas", {}).get("name", "unknown"))
    except Exception:  # noqa: BLE001 - fingerprint is best-effort
        blas = "unknown"
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "rqalpha": rqalpha.__version__,
        "blas": blas,
    }


def _equity_curve(portfolio: pd.DataFrame) -> list[dict[str, Any]]:
    curve: list[dict[str, Any]] = []
    for idx, total in zip(portfolio.index, portfolio["total_value"], strict=True):
        day = pd.Timestamp(idx).date()
        curve.append(
            {"trade_date": day.isoformat(), "total_equity": float(total)}
        )
    return curve


def _run(workdir: Path) -> dict[str, Any]:
    spec = json.loads((workdir / _SPEC_FILENAME).read_text(encoding="utf-8"))
    bars_path = workdir / _BARS_FILENAME
    bars_sha256 = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    if bars_sha256 != spec["bars_sha256"]:
        raise ValueError(
            f"bars.csv sha256 {bars_sha256[:12]} != spec {spec['bars_sha256'][:12]}"
        )

    bars = _load_bars(bars_path)
    instruments = _build_instruments(spec["instruments"])
    days = _trading_days(bars)

    fric = spec["friction"]
    board_by_obid = {s["order_book_id"]: s["board"] for s in spec["instruments"]}
    transfer_by_obid = {
        s["order_book_id"]: bool(s["transfer_fee_applies"]) for s in spec["instruments"]
    }
    friction.configure(
        board_by_obid=board_by_obid,
        transfer_fee_by_obid=transfer_by_obid,
        friction={
            "commission_rate": float(fric["commission_rate"]),
            "min_commission": float(fric["min_commission"]),
            "stamp_tax_rate": float(fric["stamp_tax_rate"]),
            "transfer_fee_rate": float(fric["transfer_fee_rate"]),
        },
        slippage_bps_by_board={
            k: float(v) for k, v in fric["slippage_bps_by_board"].items()
        },
    )
    mod.PENDING["data_source"] = PitExportDataSource(
        bars=bars, instruments=instruments, trading_days=days
    )

    orders_by_date: dict[str, list[tuple[str, int]]] = {}
    for o in spec["orders"]:
        shares = int(o["shares"])
        amount = shares if o["side"] == "BUY" else -shares
        orders_by_date.setdefault(str(o["trade_date"]), []).append(
            (o["order_book_id"], amount)
        )

    def init(context: Any) -> None:
        context.orders_by_date = orders_by_date

    def handle_bar(context: Any, bar_dict: Any) -> None:
        key = context.now.strftime("%Y%m%d")
        for obid, amount in context.orders_by_date.get(key, ()):
            order_shares(obid, amount)

    config = {
        "base": {
            "start_date": _fmt(spec["start_date"]),
            "end_date": _fmt(spec["end_date"]),
            "accounts": {"stock": float(spec["initial_capital"])},
            "frequency": "1d",
        },
        "extra": {"log_level": "error"},
        "mod": {
            "sys_progress": {"enabled": False},
            "sys_transaction_cost": {"enabled": False},
            "sys_simulation": {
                "enabled": True,
                "matching_type": "current_bar",
                "slippage_model": "rqalpha_entry.friction.QuantMindSlippage",
                "slippage": 0,
            },
            "sys_analyser": {"enabled": True, "record": True},
            "qm_inject": {"enabled": True, "lib": "rqalpha_entry.mod", "priority": 200},
        },
    }

    result = run_func(init=init, handle_bar=handle_bar, config=config)
    sa = result["sys_analyser"]
    return {
        "schema_version": _SCHEMA_VERSION,
        "engine": "rqalpha",
        "engine_version": rqalpha.__version__,
        "strategy_hash": spec["strategy_hash"],
        "bars_sha256": bars_sha256,
        "equity_curve": _equity_curve(sa["portfolio"]),
        "fill_count": int(len(sa["trades"])),
        "env_fingerprint": _env_fingerprint(),
    }


def _fmt(yyyymmdd: str) -> str:
    s = str(yyyymmdd)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _write_atomic(workdir: Path, payload: dict[str, Any]) -> None:
    """Publish the checksum sidecar BEFORE result.json, then result.json atomically.

    The runner requires the sidecar (fail-closed). Writing it first means a kill
    between the two writes leaves either {no result.json} (runner -> UNAVAILABLE)
    or {result.json + matching sidecar} — never a result.json the runner would
    adopt without an integrity check.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    raw_bytes = raw.encode("utf-8")
    (workdir / _RESULT_CHECKSUM_FILENAME).write_text(
        hashlib.sha256(raw_bytes).hexdigest(), encoding="utf-8"
    )
    tmp = workdir / (_RESULT_FILENAME + ".tmp")
    tmp.write_bytes(raw_bytes)
    os.replace(tmp, workdir / _RESULT_FILENAME)


def main() -> int:
    parser = argparse.ArgumentParser(description="rqalpha differential oracle entry")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    workdir = Path(args.workdir)
    payload = _run(workdir)
    _write_atomic(workdir, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
