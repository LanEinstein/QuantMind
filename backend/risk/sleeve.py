"""Per-sleeve position-cap context for RiskEngine check #6 (AF-005).

The value sleeve splits the position book into two independent sub-accounts —
SHORT (the fast-compounding ≤5-slot rotation) and VALUE (the long-term 埋伏 ≤3).
Check #6 (max distinct positions) becomes per-sleeve when a :class:`SleeveLimit`
is supplied; ``None`` at the ``validate_order`` boundary = value sleeve dormant =
the single ≤5 pool, BIT-IDENTICAL to the pre-AF behaviour.

The limit is resolved UPSTREAM (``backend.sleeve_policy.sleeve_limit_for`` from
the :class:`SleevePolicy`), never inside the engine: the RiskEngine stays a pure,
self-contained function with no ``backend.sleeve_policy`` import. A held
position's sleeve is derived from its existing ``entry_style`` nameplate (no new
Position field), so the engine never trusts an external sleeve tag it cannot
re-derive from the position book it already holds.
"""

from __future__ import annotations

from dataclasses import dataclass

SLEEVE_VALUE = "value"
SLEEVE_SHORT = "short"
# Mirrors backend.style.models.StyleTag.{VALUE,SHORT_TERM}.value without importing
# the style module into the pure risk layer. A position is VALUE-sleeve iff its
# entry_style equals the value token; everything else (incl. None) is SHORT.
_VALID_SLEEVES = frozenset({SLEEVE_VALUE, SLEEVE_SHORT})


@dataclass(frozen=True)
class SleeveLimit:
    """Per-sleeve caps + the order's sleeve for check #6 (immutable)."""

    order_sleeve: str
    """The sleeve this BUY would enter (``"value"`` / ``"short"``)."""
    value_style_token: str
    """The ``Position.entry_style`` value that marks the VALUE sleeve
    (``StyleTag.VALUE.value`` = ``"value"``); any other / ``None`` is SHORT."""
    value_cap: int
    short_cap: int

    def __post_init__(self) -> None:
        if self.order_sleeve not in _VALID_SLEEVES:
            raise ValueError(
                f"order_sleeve {self.order_sleeve!r} must be one of {_VALID_SLEEVES}"
            )
        if (
            not isinstance(self.value_style_token, str)
            or not self.value_style_token.strip()
        ):
            # A non-string / blank token would make ``p.entry_style == token``
            # never match a real "value" position → mispartition the book.
            raise ValueError("value_style_token must be a non-blank string")
        if not isinstance(self.value_cap, int) or isinstance(self.value_cap, bool):
            raise ValueError("value_cap must be an int")
        if not isinstance(self.short_cap, int) or isinstance(self.short_cap, bool):
            raise ValueError("short_cap must be an int")
        if self.value_cap < 0 or self.short_cap < 0:
            raise ValueError("sleeve caps must be >= 0")

    def is_value_order(self) -> bool:
        return self.order_sleeve == SLEEVE_VALUE

    def cap_for_order(self) -> int:
        """The position cap that applies to this order's sleeve."""
        return self.value_cap if self.is_value_order() else self.short_cap


__all__ = ["SLEEVE_SHORT", "SLEEVE_VALUE", "SleeveLimit"]
