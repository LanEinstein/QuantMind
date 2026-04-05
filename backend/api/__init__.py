"""QuantMind API routers."""

from backend.api.market import router as market_router
from backend.api.performance import router as performance_router
from backend.api.risk import router as risk_router
from backend.api.settings import router as settings_router

__all__ = [
    "market_router",
    "performance_router",
    "risk_router",
    "settings_router",
]
