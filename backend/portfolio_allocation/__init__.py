"""Portfolio-allocation layer (Line 1 upstream, Phase P).

Deterministic, pure-Python basket-level cash allocation that runs **upstream
of both the LLM agents and the RiskEngine** (P0-7-amendment-2026-05-30):
inverse-volatility weights (equal-weight fallback) + a conservative-tranche
deploy envelope → per-name incremental cash target → whole-lot share count.
The Line-1 provider clamps each BUY with ``min(max_compliant, target)`` —
allocation **only tightens, never relaxes** the 15% / ¥50k / cash / ≤5-order
rules, and the RiskEngine stays independently authoritative. Import isolation
(§4): no ``backend.{llm,agents,mirofish}``, and not imported by ``backend/risk``.
"""

from backend.portfolio_allocation.allocator import (
    cash_to_lots,
    compute_target_cash,
    deployable_cash,
)
from backend.portfolio_allocation.policy import (
    INVERSE_VOLATILITY,
    AllocationPolicy,
    AllocationPolicyError,
    load_allocation_policy,
)
from backend.portfolio_allocation.volatility import inverse_vol_weights

__all__ = [
    "INVERSE_VOLATILITY",
    "AllocationPolicy",
    "AllocationPolicyError",
    "cash_to_lots",
    "compute_target_cash",
    "deployable_cash",
    "inverse_vol_weights",
    "load_allocation_policy",
]
