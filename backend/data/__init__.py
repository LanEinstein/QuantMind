"""QuantMind data layer: market data, history, news, persistence.

Empty __init__ on purpose. Consumers explicit-import the submodule
they need (``from backend.data.database import MongoDBService`` etc.).

The previous version eagerly imported ``DataScheduler`` and friends,
which transitively pulled ``backend.llm.cost_tracker`` into any module
that did ``from backend.data.trading_hours import X`` — including
``backend/risk/engine.py``. That silently violated the SSoT risk
isolation redline: a fresh ``import backend.risk`` ended up loading
``backend.llm.*``. Codex P5B-shadow R5 HIGH surfaced it. Keeping
this file empty is the canonical fix; importers stay explicit and
the redline holds.
"""
