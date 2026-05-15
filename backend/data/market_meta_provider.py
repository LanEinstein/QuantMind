"""MarketMetaProvider — prev_close + live price lookup for the broker.

Used by MockBroker (E-003) to drive at-fill price-limit re-checks and
by EquityPoint MTM (E-006) for the 30-second mark-to-market loop.

The provider abstracts a two-tier fallback (Redis ≤60s → Mongo ≤300s).
``cost_price`` fallback is explicitly forbidden — the MockBroker's
in-memory cost basis is NOT a market price (P1-2.B §2 red line 6).

The class is dependency-injected: the production deployment passes a
``MongoBackedMarketMetaProvider`` instance; tests pass an
:class:`InMemoryMarketMetaProvider` with hand-set quotes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(component="data.market_meta_provider")

REDIS_FRESHNESS_SECONDS = 60
"""Maximum age (seconds) before a Redis-cached quote is considered stale
and the provider falls through to Mongo."""

MONGO_FRESHNESS_SECONDS = 300
"""Maximum age (seconds) before a Mongo-stored quote is considered too
stale to drive an at-fill price-limit re-check. After this window the
provider returns ``None`` — the broker raises (no cost_price fallback)."""


class StaleQuoteError(RuntimeError):
    """Raised when both Redis and Mongo fail the freshness window.

    Callers (MockBroker, EquityPoint MTM) interpret this as
    "live price unavailable" and follow the redline (fail-closed on
    cost_price fallback)."""


@runtime_checkable
class MarketMetaProvider(Protocol):
    """Read-only handle for prev_close + live price lookups.

    Two methods only — get_prev_close + get_current_price. Both async
    because the production implementation reads Mongo. ``get_current_price``
    must NOT fall back to cost_price (red line P1-2.B §2 #6); when no
    fresh quote exists it raises :class:`StaleQuoteError`.
    """

    async def get_prev_close(self, code: str) -> float | None:
        """Return the previous trading day's close for ``code``.

        Used by RiskEngine check 12 (limit_up_down_block) and by the
        MockBroker at-fill price-limit re-check (E-003). Returns
        ``None`` when no prior session is on file (fresh deploy or
        pre-IPO code).
        """

    async def get_current_price(
        self, code: str, *, now: datetime | None = None
    ) -> float:
        """Return the most recent live price for ``code``.

        Two-tier fallback (Redis ≤60s → Mongo ≤300s). Raises
        :class:`StaleQuoteError` when both tiers fail; never falls
        back to cost_price.
        """


# ---------------------------------------------------------------------------
# In-memory implementation for unit tests
# ---------------------------------------------------------------------------


class InMemoryMarketMetaProvider:
    """Test helper that returns hand-set quotes.

    Both ``prev_close`` and ``current_price`` accept optional
    ``stale_after``-style overrides via :meth:`set_current_price_stale`
    so tests can simulate the cost-price-fallback redline.
    """

    def __init__(
        self,
        prev_close: Mapping[str, float] | None = None,
        current_price: Mapping[str, float] | None = None,
    ) -> None:
        self._prev_close: dict[str, float] = dict(prev_close or {})
        self._current_price: dict[str, float] = dict(current_price or {})
        self._stale: set[str] = set()

    async def get_prev_close(self, code: str) -> float | None:
        return self._prev_close.get(code)

    async def get_current_price(
        self, code: str, *, now: datetime | None = None
    ) -> float:
        if code in self._stale:
            raise StaleQuoteError(
                f"no fresh quote for {code} within "
                f"{MONGO_FRESHNESS_SECONDS}s — cost_price fallback forbidden"
            )
        price = self._current_price.get(code)
        if price is None:
            raise StaleQuoteError(
                f"no quote at all for {code}; cost_price fallback forbidden"
            )
        return price

    def set_prev_close(self, code: str, price: float) -> None:
        self._prev_close[code] = price

    def set_current_price(self, code: str, price: float) -> None:
        self._current_price[code] = price
        self._stale.discard(code)

    def set_current_price_stale(self, code: str) -> None:
        """Mark ``code`` as having no fresh quote (forces StaleQuoteError)."""
        self._stale.add(code)


# ---------------------------------------------------------------------------
# Production Redis+Mongo implementation
# ---------------------------------------------------------------------------


class MongoBackedMarketMetaProvider:
    """Production provider — reads Redis first, then Mongo, fails-closed
    otherwise.

    Wire shape:
        * Redis key ``quote:{code}`` — JSON string with ``price`` +
          ``timestamp`` (ISO-8601 UTC). DataScheduler maintains a TTL of
          120s here (P0-8 §1.1).
        * Mongo collection ``market_realtime`` — full snapshot rows with
          ``code`` + ``timestamp`` + ``price``. The provider reads the
          newest row matching the code.
        * Mongo collection ``kline_daily`` — for prev_close lookup the
          provider reads the prior close from the latest closed
          daily kline row.

    The Redis pool is optional so unit tests can pass ``None`` and only
    exercise the Mongo path. ``now`` overrides the freshness window
    reference timestamp — used by deterministic tests.
    """

    def __init__(
        self,
        mongodb: Any,
        redis_client: Any | None = None,
        *,
        redis_freshness_seconds: int = REDIS_FRESHNESS_SECONDS,
        mongo_freshness_seconds: int = MONGO_FRESHNESS_SECONDS,
    ) -> None:
        self._mongodb = mongodb
        self._redis = redis_client
        self._redis_window = redis_freshness_seconds
        self._mongo_window = mongo_freshness_seconds

    async def get_prev_close(self, code: str) -> float | None:
        coll = self._mongodb._db["kline_daily"]
        cursor = coll.find({"code": code}).sort("date", -1).limit(1)
        async for doc in cursor:
            close = doc.get("close")
            if close is not None:
                return float(close)
        return None

    async def get_current_price(
        self, code: str, *, now: datetime | None = None
    ) -> float:
        # Normalise the reference time to UTC-aware so the subtraction
        # below cannot raise TypeError when the caller passes a tz-aware
        # ``now`` (Shanghai or UTC) and the Mongo / Redis timestamp comes
        # back as tz-aware or naive — codex P2.
        ref = _to_utc(now or datetime.now(UTC))

        if self._redis is not None:
            raw = await self._redis.get(f"quote:{code}")
            if raw is not None:
                price = _parse_redis_quote(raw, ref, self._redis_window)
                if price is not None:
                    return price

        coll = self._mongodb._db["market_realtime"]
        cursor = coll.find({"code": code}).sort("timestamp", -1).limit(1)
        async for doc in cursor:
            ts = doc.get("timestamp")
            price = doc.get("price")
            if ts is None or price is None:
                continue
            if not isinstance(ts, datetime):
                continue
            age = (ref - _to_utc(ts)).total_seconds()
            if 0 <= age <= self._mongo_window:
                return float(price)
            break

        raise StaleQuoteError(
            f"no fresh quote for {code} within {self._mongo_window}s "
            "(cost_price fallback forbidden — P1-2.B §2 redline 6)"
        )


def _parse_redis_quote(
    raw: str | bytes,
    ref: datetime,
    window_seconds: int,
) -> float | None:
    """Decode a Redis ``quote:{code}`` blob and return its price if fresh.

    The DataScheduler writes JSON ``{"price": .., "timestamp": "ISO"}``;
    we parse defensively because a typo in the producer would otherwise
    cause the broker to fail silently. Returns ``None`` on parse error
    or stale timestamp; the caller then falls through to Mongo.

    Both ``ref`` and the parsed ``ts`` are converted to UTC-aware
    datetimes before subtraction so a mix of tz-aware and naive
    timestamps cannot raise TypeError (codex P2).
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        price = float(payload["price"])
        ts_raw = payload["timestamp"]
        ts = _to_utc(datetime.fromisoformat(ts_raw))
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("redis_quote_parse_failed", error=str(exc))
        return None
    age = (_to_utc(ref) - ts).total_seconds()
    if age < 0 or age > window_seconds:
        return None
    return price


def _to_utc(value: datetime) -> datetime:
    """Coerce ``value`` to a UTC-aware datetime.

    Naive timestamps are assumed to be UTC (matches the legacy
    DataScheduler convention). Tz-aware values are converted via
    ``astimezone(UTC)``.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "MONGO_FRESHNESS_SECONDS",
    "REDIS_FRESHNESS_SECONDS",
    "InMemoryMarketMetaProvider",
    "MarketMetaProvider",
    "MongoBackedMarketMetaProvider",
    "StaleQuoteError",
]


# Silence unused-import warning for timedelta (kept for typing aid).
_ = timedelta
