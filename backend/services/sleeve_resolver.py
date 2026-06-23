"""Resolve a RiskEngine check#6 :class:`SleeveLimit` from the SleevePolicy (AF-005).

The services layer is the only place allowed to bridge the pure quant policy
(``backend.sleeve_policy``) and the pure risk layer (``backend.risk``): the
RiskEngine must not import the policy (it stays self-contained) and the policy
must not import risk (TID251 red line 17). This thin adapter — the natural home
next to the InstructionPlanBuilder — maps a candidate's style + the account
equity/latch into the per-sleeve caps the engine's check#6 consumes.
"""

from __future__ import annotations

from backend.risk.sleeve import SleeveLimit
from backend.sleeve_policy.policy import Sleeve, SleevePolicy
from backend.style.models import StyleTag


def sleeve_limit_for(
    policy: SleevePolicy,
    style: StyleTag,
    total_equity_yuan: float,
    *,
    latched: bool = False,
) -> SleeveLimit | None:
    """The check#6 :class:`SleeveLimit` for one BUY, or ``None`` when dormant.

    ``None`` (master switch off, or equity below the ¥50k trigger and not
    latched) makes check#6 keep the single ≤5 pool, BYTE-IDENTICAL to the pre-AF
    behaviour. When the value sleeve is active it carries the order's sleeve +
    both per-sleeve caps (SHORT ≤5 / VALUE ≤3) so check#6 caps them
    independently. Deterministic, pure.
    """
    if not policy.is_value_sleeve_active(total_equity_yuan, latched=latched):
        return None
    sleeve = policy.assign_sleeve(style)
    return SleeveLimit(
        order_sleeve=sleeve.value,
        value_style_token=StyleTag.VALUE.value,
        value_cap=policy.cap_for(Sleeve.VALUE, total_equity_yuan, latched=latched),
        short_cap=policy.cap_for(Sleeve.SHORT, total_equity_yuan, latched=latched),
    )


__all__ = ["sleeve_limit_for"]
