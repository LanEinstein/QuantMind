"""QuantMind data layer: market data, history, news, persistence.

Empty __init__ on purpose. Consumers explicit-import the submodule
they need (``from backend.data.database import MongoDBService`` etc.).

The previous version eagerly imported ``DataScheduler`` and friends,
which transitively pulled ``backend.llm.cost_tracker`` into any module
that did ``from backend.data.<helper> import X``. Keeping this file
empty preserves the P0-10 risk-isolation redline: ``backend.risk``
must never load ``backend.{llm,agents,mirofish,data}`` transitively.
The trading-hours helper moved to ``backend.utils.trading_hours`` for
exactly this reason (Phase A / A-006).
"""
