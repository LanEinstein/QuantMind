"""Shared JSON contract for the rqalpha subprocess oracle (AE-002).

The differential oracle runs rqalpha in an **isolated venv subprocess** (R-002
amendment 2026-06-14): the main env writes ``spec.json`` + ``bars.csv`` into a
temp workdir, the venv entry (:mod:`backend.backtest.rqalpha_entry`) reads them,
runs the backtest and writes ``result.json``. The two sides never share memory —
only these JSON/CSV files — so the *only* coupling is the key names below.

This module is the **main-env** half of that contract (imported by the exporter
:mod:`backend.backtest.pit_export` and the runner
:class:`backend.strategy_evolution.backtest_oracle.RqalphaBacktestRunner`). The
venv entry deliberately re-declares the same literals (it cannot import
``backend.*``); the end-to-end integration test exercises the round trip, so a
drift between the two sides fails the suite rather than silently mis-parsing.

The module is pure (stdlib only) — no ``rqalpha``, no ``backend.data`` — so it is
safe to import anywhere in the main env.
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final = 1
"""Contract version. Bump on any breaking key change (both sides must agree)."""

# -- filenames inside the temp workdir --------------------------------------
SPEC_FILENAME: Final = "spec.json"
BARS_FILENAME: Final = "bars.csv"
RESULT_FILENAME: Final = "result.json"
RESULT_CHECKSUM_FILENAME: Final = "result.json.sha256"

# -- spec.json keys (main env -> venv) --------------------------------------
SPEC_SCHEMA_VERSION: Final = "schema_version"
SPEC_START_DATE: Final = "start_date"  # YYYYMMDD
SPEC_END_DATE: Final = "end_date"  # YYYYMMDD
SPEC_INITIAL_CAPITAL: Final = "initial_capital"
SPEC_STRATEGY_HASH: Final = "strategy_hash"
SPEC_BARS_SHA256: Final = "bars_sha256"
SPEC_INSTRUMENTS: Final = "instruments"
SPEC_ORDERS: Final = "orders"
SPEC_FRICTION: Final = "friction"

# instrument sub-keys
INS_ORDER_BOOK_ID: Final = "order_book_id"  # rqalpha .XSHG/.XSHE id
INS_TYPE: Final = "instrument_type"  # "CS" | "ETF"
INS_BOARD: Final = "board"  # "sh_main" | "sz_main" | "chuangye" | "etf"
INS_LISTED_DATE: Final = "listed_date"  # YYYY-MM-DD
INS_DE_LISTED_DATE: Final = "de_listed_date"  # YYYY-MM-DD
INS_ROUND_LOT: Final = "round_lot"
INS_TRANSFER_FEE_APPLIES: Final = "transfer_fee_applies"

# order sub-keys
ORD_TRADE_DATE: Final = "trade_date"  # YYYYMMDD
ORD_ORDER_BOOK_ID: Final = "order_book_id"
ORD_SIDE: Final = "side"  # "BUY" | "SELL"
ORD_SHARES: Final = "shares"

# friction sub-keys (mirrors config/broker.yaml — single source of truth)
FRIC_COMMISSION_RATE: Final = "commission_rate"
FRIC_MIN_COMMISSION: Final = "min_commission"
FRIC_STAMP_TAX_RATE: Final = "stamp_tax_rate"
FRIC_TRANSFER_FEE_RATE: Final = "transfer_fee_rate"
FRIC_SLIPPAGE_BPS_BY_BOARD: Final = "slippage_bps_by_board"

# -- result.json keys (venv -> main env) ------------------------------------
RES_SCHEMA_VERSION: Final = "schema_version"
RES_ENGINE: Final = "engine"
RES_ENGINE_VERSION: Final = "engine_version"
RES_STRATEGY_HASH: Final = "strategy_hash"
RES_BARS_SHA256: Final = "bars_sha256"
RES_EQUITY_CURVE: Final = "equity_curve"  # [{trade_date, total_equity}]
RES_FILL_COUNT: Final = "fill_count"
RES_ENV_FINGERPRINT: Final = "env_fingerprint"  # {python, numpy, pandas, rqalpha}

EQ_TRADE_DATE: Final = "trade_date"  # YYYY-MM-DD (canonical for EquityDay)
EQ_TOTAL_EQUITY: Final = "total_equity"

# bars.csv columns (byte-stable CSV — pyarrow/parquet is absent from both the
# main and the oracle venv, and installing it would move the main-env baseline;
# the R0 §3 red line is content-addressed + checksum + same-source, which a
# canonical CSV satisfies exactly. Columns are fixed + ordered so the sha256 is
# reproducible).
BARS_COLUMNS: Final = (
    INS_ORDER_BOOK_ID,
    "trade_date",  # YYYYMMDD int
    "open",
    "high",
    "low",
    "close",
    "volume",
    "total_turnover",
    "limit_up",
    "limit_down",
)

ENGINE_NAME: Final = "rqalpha"

__all__ = [
    "BARS_COLUMNS",
    "BARS_FILENAME",
    "ENGINE_NAME",
    "EQ_TOTAL_EQUITY",
    "EQ_TRADE_DATE",
    "FRIC_COMMISSION_RATE",
    "FRIC_MIN_COMMISSION",
    "FRIC_SLIPPAGE_BPS_BY_BOARD",
    "FRIC_STAMP_TAX_RATE",
    "FRIC_TRANSFER_FEE_RATE",
    "INS_BOARD",
    "INS_DE_LISTED_DATE",
    "INS_LISTED_DATE",
    "INS_ORDER_BOOK_ID",
    "INS_ROUND_LOT",
    "INS_TRANSFER_FEE_APPLIES",
    "INS_TYPE",
    "ORD_ORDER_BOOK_ID",
    "ORD_SHARES",
    "ORD_SIDE",
    "ORD_TRADE_DATE",
    "RESULT_CHECKSUM_FILENAME",
    "RESULT_FILENAME",
    "RES_BARS_SHA256",
    "RES_ENGINE",
    "RES_ENGINE_VERSION",
    "RES_ENV_FINGERPRINT",
    "RES_EQUITY_CURVE",
    "RES_FILL_COUNT",
    "RES_SCHEMA_VERSION",
    "RES_STRATEGY_HASH",
    "SCHEMA_VERSION",
    "SPEC_BARS_SHA256",
    "SPEC_END_DATE",
    "SPEC_FILENAME",
    "SPEC_FRICTION",
    "SPEC_INITIAL_CAPITAL",
    "SPEC_INSTRUMENTS",
    "SPEC_ORDERS",
    "SPEC_SCHEMA_VERSION",
    "SPEC_START_DATE",
    "SPEC_STRATEGY_HASH",
]
