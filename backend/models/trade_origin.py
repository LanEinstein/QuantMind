"""TradeOrigin — provenance of a trade for the 3-way performance split.

Lives in the leaf ``backend.models`` layer (not ``backend.broker``) because
both :class:`backend.broker.models.Trade` and
:class:`backend.models.manual_trade.ExternalExecutionEvent` reference it, and
``backend.models`` must not depend on ``backend.broker``
(P2-2 §2 import-isolation red line).
"""

from __future__ import annotations

from enum import StrEnum


class TradeOrigin(StrEnum):
    """Provenance of a trade for the 3-way performance split (AD-005).

    P1-2.A-amendment-2026-06-12 / codex P0-7: user-discretionary fills must
    never inflate the system's measured performance (go-live readiness,
    acceptance strategy gates, self-evolution scoring all read the
    ``SYSTEM_SUGGESTED`` bucket only). ``RECONCILIATION_RESET`` is a
    snapshot rewrite that produces no :class:`~backend.broker.models.Trade`
    row — the value is kept for the front-end's three-bucket view + future
    attribution.
    """

    SYSTEM_SUGGESTED = "system_suggested"
    USER_DISCRETIONARY = "user_discretionary"
    RECONCILIATION_RESET = "reconciliation_reset"


__all__ = ["TradeOrigin"]
