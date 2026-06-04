"""PILOT cond9 — startup data-layer reachability probe.

P0-6-amendment-2026-05-29 §1 (owner-chosen "infra reachability" semantics):
the PILOT readiness gate runs once at backend startup and must NOT re-gate
per-code freshness (the builder early-return + ``DataQualityState`` own that at
trade time). cond9 proves only that the live quote vendors are *reachable* for
the three mandatory broad-based ETFs (P0-9 §2 redline locks them into every
universe). A canary is "unreachable" only when BOTH dual-source vendor legs
fail to serve any data for the code (vendor outage) — staleness / divergence /
freshness are time-of-day artifacts (pre-open quotes are always stale) and
deliberately do not block boot, so the owner can start the backend before the
09:30 open.

P0-6-amendment-2026-06-04: the probe now consumes the dedicated
``probe_quote_vendor_reachability`` reachability method instead of the
trading-path ``get_stock_realtime_dual`` — the latter fail-closes on pre-open
rows (sina ``PRICE == 0``, adata empty frame), which made "not yet open"
indistinguishable from "vendor outage" and refused pre-open boots,
contradicting the §1 intent above. Trading-path validation is unchanged.

This is a focused helper, not the full 7-signal ``DataQualityProvider`` (whose
four concrete probes do not exist yet — see the amendment §1.2 / §4). It maps
exactly onto the chosen ``quote_unavailable``-only semantics.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import structlog

log = structlog.get_logger(component="pilot_data_probe")

# P0-9 §2 redline — the three mandatory broad-based ETFs, always in universe.
MANDATORY_ETF_CANARIES: tuple[str, ...] = ("510300", "510500", "159949")


class _VendorReachabilityProbe(Protocol):
    """Structural type for the slice of MarketDataService cond9 needs.

    The method returns plain booleans on purpose: cond9 only needs "did each
    vendor serve data for the code?", and this module lives in
    ``backend/services`` which is forbidden from importing ``backend.data``
    (P2-2 §2 redline 17). ``market_data`` is dependency-injected, so the real
    ``MarketDataService.probe_quote_vendor_reachability`` satisfies this
    structural type without an import (P0-6-amendment-2026-06-04).
    """

    async def probe_quote_vendor_reachability(self, code: str) -> tuple[bool, bool]: ...


async def _code_reachable(market_data: _VendorReachabilityProbe, code: str) -> bool:
    """Return True when at least one vendor leg serves data for ``code``.

    Any exception from the probe path is treated as unreachable (fail-closed)
    for that code — a probe that cannot complete is not evidence of a healthy
    data layer.
    """
    try:
        primary_ok, fallback_ok = await market_data.probe_quote_vendor_reachability(
            code
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed on any probe error
        log.warning("cond9_canary_probe_raised", code=code, error=str(exc))
        return False
    return primary_ok or fallback_ok


async def canary_quotes_reachable(
    market_data: _VendorReachabilityProbe | None,
    codes: tuple[str, ...] = MANDATORY_ETF_CANARIES,
) -> bool:
    """Return True when every canary code has at least one reachable quote leg.

    Args:
        market_data: The live ``MarketDataService`` (or any object exposing
            ``probe_quote_vendor_reachability``). ``None`` means the data
            layer is not wired → fail-closed ``False``.
        codes: Canary codes to probe; defaults to the three mandatory ETFs.

    Returns:
        ``True`` only when no canary is ``quote_unavailable`` (both vendor
        legs failing to serve data — P0-6-amendment-2026-06-04). ``False``
        if ``market_data`` is missing, ``codes`` is empty, or any canary is
        unreachable.
    """
    if market_data is None:
        log.warning("cond9_market_data_unwired")
        return False
    if not codes:
        log.warning("cond9_no_canary_codes")
        return False

    results = await asyncio.gather(
        *(_code_reachable(market_data, code) for code in codes)
    )
    unreachable = [code for code, ok in zip(codes, results, strict=True) if not ok]
    if unreachable:
        log.warning("cond9_canaries_unreachable", codes=unreachable)
        return False
    return True
