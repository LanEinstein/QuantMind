"""As-of forward-adjusted close reconstruction from PIT snapshots (AE-001).

R0 §3 / amendment §2.2 forbid persisting *adjusted-only* prices: a later
split/dividend would silently rewrite history (look-ahead leak). Instead we
store the **raw un-adjusted** ``daily`` close plus an independent
``adj_factor`` pin, and reconstruct the forward-adjusted (qfq) view *as of* a
reference date on demand — using only the factors known on/before that date.

Reconstruction is bit-exact (``Decimal`` arithmetic, no float drift): the same
stored bytes always yield the same adjusted series, which is what makes
backtest replay reproducible. The qfq formula is the zipline/qlib convention:

    qfq_close(d) = raw_close(d) * adj_factor(d) / adj_factor(asof)

where ``asof`` is the most recent stored trading day ``<=`` the reference date
(so a split *after* the reference date never leaks backwards).
"""

from __future__ import annotations

from decimal import Decimal

from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.marketdata_snapshot.store import SnapshotStore


def _decimal(value: object) -> Decimal:
    """Parse a cell to Decimal via its string form (no float rounding)."""
    return Decimal(str(value).strip())


def reconstruct_adjusted_close(
    store: SnapshotStore,
    ts_code: str,
    trade_dates: list[str],
    *,
    asof_date: str,
) -> dict[str, Decimal]:
    """Forward-adjusted (qfq) close for ``ts_code`` as of ``asof_date``.

    Reads the raw ``daily`` close and the ``adj_factor`` pin for ``ts_code``
    from each stored trade date ``<= asof_date`` (PIT) and returns the qfq
    close keyed by trade date.

    Args:
        store: The K-002 snapshot store (verify-before-adopt on read).
        ts_code: e.g. ``600519.SH``.
        trade_dates: Candidate trade dates to consider (the ingested days).
        asof_date: The PIT reference date (``YYYYMMDD``); factors after this
            date are ignored.

    Returns:
        ``{trade_date: qfq_close}`` for every date that had both a daily and
        an adj_factor row for ``ts_code``; empty dict if none.
    """
    raw_close: dict[str, Decimal] = {}
    factor: dict[str, Decimal] = {}
    for day in sorted(set(trade_dates)):
        if day > asof_date:
            continue
        daily_snap = store.latest(
            vendor=VENDOR, endpoint="daily", trade_date=day
        )
        adj_snap = store.latest(
            vendor=VENDOR, endpoint="adj_factor", trade_date=day
        )
        if daily_snap is None or adj_snap is None:
            continue
        daily_df = parse_csv_bytes(daily_snap.raw_payload)
        adj_df = parse_csv_bytes(adj_snap.raw_payload)
        crow = daily_df[daily_df["ts_code"].astype(str) == ts_code]
        arow = adj_df[adj_df["ts_code"].astype(str) == ts_code]
        if crow.empty or arow.empty:
            continue
        raw_close[day] = _decimal(crow.iloc[0]["close"])
        factor[day] = _decimal(arow.iloc[0]["adj_factor"])

    if not factor:
        return {}
    asof_factor = factor[max(factor)]
    return {
        day: (raw_close[day] * factor[day] / asof_factor) for day in raw_close
    }


__all__ = ["reconstruct_adjusted_close"]
