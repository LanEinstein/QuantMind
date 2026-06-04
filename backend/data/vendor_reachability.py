"""Boot-gate vendor reachability probe (cond9 probe-specific semantics).

P0-6-amendment-2026-06-04: the PILOT cond9 canary probe needs *infra
reachability* — "did each quote vendor serve data for this code?" — NOT a
tradeable quote. Pre-open the sina leg answers with a real row whose
``PRICE == 0`` (no trade yet); the trading-path parser
(``_tushare_sina_row_to_quote``) correctly fail-closes on it, which made
"not yet open" indistinguishable from "vendor outage" under the old
``get_stock_realtime_dual`` reuse and refused pre-open boots — contradicting
the 2026-05-29 amendment's stated intent (the owner can start the backend
before the 09:30 open).

A leg is reachable iff its fetch returned a NON-EMPTY frame for the code;
transport errors, ``None`` and empty frames stay fail-closed ``False``.
Only booleans leave this module — no relaxed-validation quote object can
leak into MTM / divergence / decision paths (those still go through
``get_stock_realtime_dual``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pandas as pd
import structlog

log = structlog.get_logger(component="vendor_reachability")

FetchFn = Callable[[str], pd.DataFrame]


def _leg_served(
    result: pd.DataFrame | BaseException | None, *, code: str, leg: str
) -> bool:
    """True iff the vendor returned at least one row for ``code``.

    An exception (transport / SDK / universe error) or an empty / ``None``
    frame proves nothing about the vendor serving data → fail-closed False.
    """
    if isinstance(result, BaseException):
        log.warning("vendor_probe_leg_failed", code=code, leg=leg, error=str(result))
        return False
    return result is not None and not result.empty


async def probe_dual_vendor_reachability(
    code: str,
    *,
    primary_fetch: FetchFn,
    fallback_fetch: FetchFn,
) -> tuple[bool, bool]:
    """Probe both dual-source vendor legs for ``code`` (row presence only).

    Mirrors the concurrent ``asyncio.gather(asyncio.to_thread(...))``
    structure of ``get_stock_realtime_dual`` but deliberately skips quote
    validation: a pre-open sina row with ``PRICE == 0`` IS evidence the
    vendor is alive and serving data for the code (the whole point of the
    2026-06-04 amendment). Fetchers are injected so the probe is a pure,
    directly-testable function.

    Returns:
        ``(primary_served, fallback_served)`` booleans — the caller
        (``pilot_data_probe._code_reachable``) treats the code as reachable
        when either leg is True.
    """
    primary_df, fallback_df = await asyncio.gather(
        asyncio.to_thread(primary_fetch, code),
        asyncio.to_thread(fallback_fetch, code),
        return_exceptions=True,
    )
    return (
        _leg_served(primary_df, code=code, leg="primary"),
        _leg_served(fallback_df, code=code, leg="fallback"),
    )
