"""StockMetadata + Board enum — board / ST / instrument classification.

Lives in ``backend/risk/`` because RiskEngine 14-check 11/12 consume it
and ``backend/risk/`` cannot import ``backend.data`` (P0-7 §2 redline 9).

The classification helpers (``classify_board`` / ``is_st`` /
``get_price_limit_pct``) belong to the data layer (``backend/data/
stock_metadata.py``, lands with D-002/D-003) which will import
``StockMetadata`` / ``Board`` from this module — one-way risk → data
import direction (P0-7 §2 redline 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Board(StrEnum):
    """A-share board identifiers used by the universe whitelist.

    Values are the canonical lowercase string identifiers also used as
    keys in ``UniverseConfig.price_limit_pct_by_board`` and
    ``UniverseConfig.allowed_boards``.
    """

    SH_MAIN = "sh_main"
    """Shanghai main board: 600 / 601 / 603 / 605."""

    SZ_MAIN = "sz_main"
    """Shenzhen main board: 000 / 002."""

    CHUANGYE = "chuangye"
    """ChiNext (Shenzhen growth board): 300 / 301."""

    KCHUANG = "kchuang"
    """STAR board (Shanghai sci-tech): 688. Phase-A excluded from
    ``allowed_boards``; requires P0-7 amendment to add back."""

    BEIJIAO = "beijiao"
    """Beijing Stock Exchange: 83 / 87 / 88 / 92. Excluded Phase-A."""

    ETF = "etf"
    """Exchange-traded funds. Code-range hint is broad (15/16/18/50/51/
    52/56/58); the data layer should cross-verify with the upstream
    quote provider's ``instrument_type`` field."""

    CONVERTIBLE_BOND = "convertible_bond"
    """Convertible bonds: 11x / 12x. Excluded Phase-A (T+0 semantics +
    different price-limit semantics make them unsafe for the 14-check
    contract; requires amendment to admit)."""

    UNKNOWN = "unknown"
    """Fallback when the code prefix matches no known board. Universe
    check rejects this — there is no "default allow" path."""


@dataclass(frozen=True, slots=True)
class StockMetadata:
    """Per-stock universe + price-limit input to RiskEngine 14-check.

    Frozen + slots to lock down accidental mutation. The data layer
    builds an instance from akshare / adata snapshots and hands it to
    InstructionPlanBuilder, which forwards it into
    ``RiskEngine.validate_order``.
    """

    code: str
    """6-digit stock code matching the order's code field."""

    name: str
    """Display name. Used by check 11 alongside ``is_st`` because ST
    naming convention (``ST`` / ``*ST`` / ``S*ST`` prefix) is the
    primary observable signal."""

    board: Board
    """Resolved board classification. Drives universe whitelist (check
    11) and per-board price-limit lookup (check 2 / check 12)."""

    is_st: bool
    """True if the stock is currently flagged ST / *ST / S*ST. Computed
    by the data layer from both the name prefix and the upstream ST list
    (double-track verification per P0-7 §1.4.1)."""

    instrument_type: str
    """Upstream-reported instrument category — ``"stock"`` / ``"etf"`` /
    ``"bond"`` / ``"unknown"``. Drives ETF / convertible-bond
    classification in the data layer; engine code reads it only as a
    cross-check."""
