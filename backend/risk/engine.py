"""Hard-coded risk control engine — PURE PYTHON, ZERO LLM DEPENDENCY.

Every trade instruction from any LLM agent MUST pass through this engine.
This module must NEVER import from backend/llm/, backend/agents/, or
backend/mirofish/. All rules are enforced by code, not by LLM output.
"""

from __future__ import annotations

import datetime as dt
import re

import structlog

from backend.broker.models import (
    AccountInfo,
    Order,
    OrderDirection,
    OrderType,
    Position,
    RiskConfig,
    ValidationResult,
)
from backend.utils.trading_hours import is_trading_hours

log = structlog.get_logger(component="risk.engine")

_CODE_RE = re.compile(r"^\d{6}$")


class RiskEngine:
    """Hard-coded risk engine enforcing A-share trading rules.

    All checks are pure Python with no LLM dependency.
    Parameters are loaded from config/risk.yaml via RiskConfig.
    """

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def validate_order(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None = None,
        now: dt.datetime | None = None,
    ) -> ValidationResult:
        """Run the 7-check validation chain. First failure short-circuits.

        Args:
            order: The order to validate.
            account: Current account snapshot.
            positions: Current positions tuple.
            prev_close: Previous close price for the stock (None if unknown).
            now: Current time (injectable for testing).

        Returns:
            ValidationResult — passed=True if all checks pass.
        """
        checks = [
            self._check_code_validity,
            self._check_price_reasonability,
            self._check_volume_validity,
            self._check_fund_sufficiency,
            self._check_position_limit,
            self._check_total_position_limit,
            self._check_trading_time,
        ]
        for check in checks:
            result = check(order, account, positions, prev_close, now)
            if not result.passed:
                log.warning(
                    "order_rejected",
                    rule=result.rule_name,
                    code=order.code,
                    message=result.message,
                )
                return result

        log.info("order_validated", code=order.code, direction=order.direction)
        return ValidationResult(passed=True)

    def _check_code_validity(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 1: stock code must be exactly 6 digits."""
        if not _CODE_RE.match(order.code):
            return ValidationResult(
                passed=False,
                rule_name="code_validity",
                message=f"Invalid stock code: {order.code}",
            )
        return ValidationResult(passed=True)

    def _check_price_reasonability(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 2: order price within +-5% of prev_close."""
        if order.order_type == OrderType.MARKET:
            return ValidationResult(passed=True)
        if prev_close is None or prev_close <= 0:
            return ValidationResult(passed=True)

        limit = self._config.position_limits.price_deviation_limit
        deviation = abs(order.price - prev_close) / prev_close
        if deviation > limit:
            return ValidationResult(
                passed=False,
                rule_name="price_reasonability",
                message=(
                    f"Price {order.price} deviates {deviation:.1%} "
                    f"from prev_close {prev_close} (limit: +-{limit:.0%})"
                ),
            )
        return ValidationResult(passed=True)

    def _check_volume_validity(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 3: volume must be positive and multiple of lot size."""
        lot = self._config.position_limits.volume_lot_size
        if order.volume <= 0 or order.volume % lot != 0:
            return ValidationResult(
                passed=False,
                rule_name="volume_validity",
                message=(
                    f"Volume {order.volume} must be a positive "
                    f"multiple of {lot}"
                ),
            )
        return ValidationResult(passed=True)

    def _check_fund_sufficiency(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 4: sufficient cash (BUY) or available shares (SELL)."""
        if order.direction == OrderDirection.BUY:
            estimated_cost = order.price * order.volume * 1.001
            if account.available_cash < estimated_cost:
                return ValidationResult(
                    passed=False,
                    rule_name="fund_sufficiency",
                    message=(
                        f"Insufficient funds: need {estimated_cost:.2f}, "
                        f"available {account.available_cash:.2f}"
                    ),
                )
        else:
            pos = next(
                (p for p in positions if p.code == order.code), None
            )
            if pos is None:
                return ValidationResult(
                    passed=False,
                    rule_name="fund_sufficiency",
                    message=f"No position for {order.code}",
                )
            if pos.available_volume < order.volume:
                return ValidationResult(
                    passed=False,
                    rule_name="fund_sufficiency",
                    message=(
                        f"Insufficient available shares: "
                        f"need {order.volume}, available {pos.available_volume}"
                    ),
                )
        return ValidationResult(passed=True)

    def _check_position_limit(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 5: single stock <= max_single_stock_pct of total_assets."""
        if order.direction == OrderDirection.SELL:
            return ValidationResult(passed=True)
        if account.total_assets <= 0:
            return ValidationResult(
                passed=False,
                rule_name="position_limit",
                message="Cannot trade with zero total assets",
            )

        existing = next(
            (p for p in positions if p.code == order.code), None
        )
        # Use order.price to revalue existing shares for consistent exposure
        existing_shares = existing.volume if existing else 0
        new_value = (existing_shares + order.volume) * order.price
        ratio = new_value / account.total_assets
        limit = self._config.position_limits.max_single_stock_pct

        if ratio > limit:
            return ValidationResult(
                passed=False,
                rule_name="position_limit",
                message=(
                    f"Position {order.code} would be {ratio:.1%} "
                    f"of portfolio (limit: {limit:.0%})"
                ),
            )
        return ValidationResult(passed=True)

    def _check_total_position_limit(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 6: max number of distinct positions."""
        if order.direction == OrderDirection.SELL:
            return ValidationResult(passed=True)

        held_codes = {p.code for p in positions if p.volume > 0}
        if order.code in held_codes:
            return ValidationResult(passed=True)

        max_pos = self._config.position_limits.max_total_positions
        if len(held_codes) >= max_pos:
            return ValidationResult(
                passed=False,
                rule_name="total_position_limit",
                message=(
                    f"Already holding {len(held_codes)} positions "
                    f"(max: {max_pos})"
                ),
            )
        return ValidationResult(passed=True)

    def _check_trading_time(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
    ) -> ValidationResult:
        """Check 7: must be within A-share trading hours."""
        if not is_trading_hours(now):
            return ValidationResult(
                passed=False,
                rule_name="trading_time",
                message="Order rejected: outside trading hours",
            )
        return ValidationResult(passed=True)
