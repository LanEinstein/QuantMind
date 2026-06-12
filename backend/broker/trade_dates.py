"""Trade-date derivation + per-date buy bookkeeping for T+1 accounting.

P0-4-amendment-2026-06-04: the external-report T+1 guard must key on the
**instruction's trade date** (``QM-YYYYMMDD-…``), not the report's parse
timestamp — a 盘后/次日补录 report still refers to the instruction-date
execution (plans are human-executed same day and expire EOD). Shared by the
live :class:`~backend.broker.mock_broker.MockBroker` and the persistence
recovery replay so a restart rebuilds the same per-date buy record the
guard consumes (codex cycle-3 P1).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from backend.utils.trading_hours import SHANGHAI

# AD-005 (P1-2.A-amendment-2026-06-12): the manual-trade path mints
# ``UT-YYYYMMDD-…`` ids in the same date position, so the same T+1 trade-date
# derivation applies — keeping the embedded date authoritative makes the live
# apply and the recovery replay agree bit-for-bit regardless of timestamp tz.
_INSTRUCTION_TRADE_DATE_RE = re.compile(r"^(?:QM|UT)-(\d{8})-")

# Per-position per-date buy entries kept for the T+1 guard. Legit reports
# reference today / very recent dates; older entries are settled and only
# the over-holding guard applies to them.
BOUGHT_BY_DATE_KEEP: int = 5


def instruction_trade_date(order_id_hint: str, fallback: datetime) -> date:
    """Trade date for T+1 accounting on the external-report path.

    Parses the ``QM-YYYYMMDD-`` prefix of the instruction id; falls back to
    the fill timestamp's Shanghai date when the hint carries no parseable
    date (defensive — synthetic/test hints).
    """
    match = _INSTRUCTION_TRADE_DATE_RE.match(order_id_hint or "")
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    return fallback.astimezone(SHANGHAI).date()


def record_buy_date(
    bought_by_date: dict[date, int], traded_date: date, volume: int
) -> None:
    """Accumulate ``volume`` under ``traded_date`` and prune old entries.

    In-place on purpose: the per-position dict lives inside the broker /
    recovery mutable position records. Pruning keeps the oldest entries
    out once more than :data:`BOUGHT_BY_DATE_KEEP` dates accumulate —
    entries that old are settled, so only the over-holding guard governs
    them.
    """
    bought_by_date[traded_date] = bought_by_date.get(traded_date, 0) + volume
    while len(bought_by_date) > BOUGHT_BY_DATE_KEEP:
        del bought_by_date[min(bought_by_date)]
