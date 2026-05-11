"""Cross-cutting stdlib-only helpers.

Empty __init__ on purpose. Consumers explicit-import the submodule
they need (``from backend.utils.trading_hours import is_trading_hours``).
This package exists so that ``backend/risk/`` can pull in pure
timezone / calendar utilities without violating the P0-10 redline that
forbids ``backend.risk`` from importing ``backend.{llm,agents,mirofish,data}``.
"""
