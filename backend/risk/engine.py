"""Hard-coded risk control engine — PURE PYTHON, ZERO LLM DEPENDENCY.

Every trade instruction from any LLM agent MUST pass through this engine.
This module must NEVER import from backend/llm/, backend/agents/,
backend/data/, or backend/mirofish/. All rules are enforced by code, not by
LLM output.

P0-7 expanded the original 7-check chain to 14 checks:
    1. code_validity                 8.  total_position_pct
    2. price_reasonability           9.  single_instruction_amount
    3. volume_validity               10. daily_new_instruction_count
    4. fund_sufficiency              11. universe_whitelist
    5. position_limit                12. limit_up_down_block
    6. total_position_limit          13. daily_loss_halt
    7. trading_time                  14. consecutive_loss_halt

Checks 8-14 require ``DailyTradingState`` (today counters, current quote,
halt state) and ``StockMetadata`` (board, ST flag). InstructionPlanBuilder
assembles these from MockBroker / decision_ledger / quote provider and
passes them in — RiskEngine remains a pure function with no IO (P0-7 §2
redline 9).

Backward compatibility: when both ``daily_state`` and ``stock_meta`` are
``None`` (legacy 7-check callers), checks 8-14 are skipped. Once either is
provided the full 14-check chain runs and check 11 / 12 fail-closed if
their specific inputs are missing (P0-7 §2 redline 13).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from decimal import ROUND_HALF_UP, Decimal

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
from backend.risk.daily_state import DailyTradingState
from backend.risk.stock_meta import StockMetadata
from backend.utils.trading_hours import is_trading_hours

log = structlog.get_logger(component="risk.engine")

_CODE_RE = re.compile(r"^\d{6}$")


_TICK = Decimal("0.01")


def _exchange_price_limit(prev_close: float, pct: float, *, upper: bool) -> float:
    """Compute the published A-share limit-up/down price.

    Shanghai/Shenzhen multiply ``prev_close`` by ``1 ± pct`` symbolically
    and then round to 0.01 RMB using 四舍五入 (round half away from
    zero). Two float-only pitfalls we must avoid:

    1. ``1.65 * 0.9`` evaluates to ``1.4849999...`` in IEEE 754, so a
       naive ``round(_, 2)`` collapses the published ``1.49`` to
       ``1.48``. We do the multiplication entirely in ``Decimal`` to
       preserve the symbolic value.
    2. Python's ``round`` uses banker's rounding, which sends ``1.485``
       to ``1.48`` instead of the exchange's ``1.49``. ``Decimal`` with
       ``ROUND_HALF_UP`` matches the published convention.

    Codex cycle 1 P1.
    """
    base = Decimal(str(prev_close))
    delta = Decimal(str(pct))
    factor = (Decimal("1") + delta) if upper else (Decimal("1") - delta)
    return float((base * factor).quantize(_TICK, rounding=ROUND_HALF_UP))


class RiskEngine:
    """Hard-coded 14-check risk engine for A-share trading.

    All checks are pure Python with no LLM dependency. Parameters come from
    ``RiskConfig`` (frozen Pydantic v2 model loaded from
    ``config/risk.yaml``; runtime-immutable per P0-7 §2 redline 1).
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
        daily_state: DailyTradingState | None = None,
        stock_meta: StockMetadata | None = None,
        concentration_exception: bool = False,
    ) -> ValidationResult:
        """Run the 14-check validation chain. First failure short-circuits.

        Args:
            order: The order to validate.
            account: Current account snapshot.
            positions: Current positions tuple.
            prev_close: Previous close price (None if unknown).
            now: Current time (injectable for testing).
            daily_state: Per-day counters + halt state. ``None`` is treated
                as a legacy 7-check call when ``stock_meta`` is also
                ``None``; otherwise checks 10/13/14 fail-closed per their
                own None handling.
            stock_meta: Board + ST classification. ``None`` is treated as a
                legacy 7-check call when ``daily_state`` is also ``None``;
                otherwise check 11 fail-closes (P0-7 §2 redline 13).
            concentration_exception: Upstream ``BudgetTierPolicy`` intent
                flag (P0-7-amendment-2026-05-24). It only *enables* the
                single-stock check (check 5) to consider an over-15% ETF
                exception; the engine still independently re-derives
                ETF + whitelist + ≤1-lot from its own config + stock_meta,
                so the flag alone never bypasses the limit. Defaults False
                (every existing caller keeps the strict P0-7 15% rule).

        Returns:
            ValidationResult — ``passed=True`` if all (applicable) checks
            pass; otherwise the first failing rule's result.
        """
        if stock_meta is not None and stock_meta.code != order.code:
            # Stale / wrong metadata would let a forbidden board (e.g.
            # KCHUANG 688xxx) slip through the universe filter — and
            # check 2 / check 12 would consult the wrong board's
            # price-limit pct. Reject before any check runs so the
            # rule_name pinpoints the upstream builder bug instead of
            # masquerading as a universe miss. Codex cycle 1 P1.
            log.warning(
                "stock_meta_code_mismatch",
                order_code=order.code, meta_code=stock_meta.code,
            )
            return ValidationResult(
                passed=False,
                rule_name="stock_meta_mismatch",
                message=(
                    f"stock_meta code mismatch: order={order.code} "
                    f"meta={stock_meta.code}"
                ),
            )

        legacy_mode = daily_state is None and stock_meta is None

        base_checks = (
            self._check_code_validity,
            self._check_price_reasonability,
            self._check_volume_validity,
            self._check_fund_sufficiency,
            self._check_position_limit,
            self._check_total_position_limit,
            self._check_trading_time,
        )
        extended_checks = (
            self._check_total_position_pct,
            self._check_single_instruction_amount,
            self._check_daily_new_instruction_count,
            self._check_universe_whitelist,
            self._check_limit_up_down_block,
            self._check_daily_loss_halt,
            self._check_consecutive_loss_halt,
        )

        checks = base_checks if legacy_mode else base_checks + extended_checks
        granted_exception: ValidationResult | None = None
        for check in checks:
            # Identify check 5 by name — ``is`` on a bound method is always
            # False (a fresh bound-method object is created on each
            # attribute access), so compare the stable __name__ instead.
            if check.__name__ == "_check_position_limit":
                # Check 5 is the only budget-aware check: it takes the
                # extra concentration_exception flag. Special-cased here so
                # the other 13 checks keep the uniform 7-arg signature.
                result = self._check_position_limit(
                    order, account, positions, prev_close, now,
                    daily_state, stock_meta, concentration_exception,
                )
                # Preserve a granted concentration exception so its
                # ``concentration_exception_granted`` reason survives into
                # the top-level result (and the builder's 14-row summary /
                # Feishu confirmation) instead of being lost behind a bare
                # passed=True aggregate (codex L-004 P2). A normal pass has
                # an empty message and is not carried.
                if result.passed and result.message:
                    granted_exception = result
            else:
                result = check(
                    order, account, positions, prev_close, now,
                    daily_state, stock_meta,
                )
            if not result.passed:
                log.warning(
                    "order_rejected",
                    rule=result.rule_name,
                    code=order.code,
                    message=result.message,
                )
                return result

        log.info("order_validated", code=order.code, direction=order.direction)
        if granted_exception is not None:
            return granted_exception
        return ValidationResult(passed=True)

    # ------------------------------------------------------------------
    # Checks 1-7 — original chain (P0-3 lock)
    # ------------------------------------------------------------------

    def _check_code_validity(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
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
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 2: limit-order price within board-specific limit.

        Per P0-7 §1.4.2 the limit is board-keyed (``sh_main`` / ``sz_main``
        / ``etf`` 10%, ``chuangye`` 20%). Without ``stock_meta`` we fall
        back to ``PositionLimitsConfig.price_deviation_limit`` (kept for
        the legacy 7-check path; builder always supplies stock_meta).
        """
        if order.order_type == OrderType.MARKET:
            return ValidationResult(passed=True)
        # NaN / +-Inf prev_close would let the later
        # ``deviation > limit`` comparison silently return False and pass
        # an unbounded price through. Treat malformed values like None.
        # Codex cycle 2 P1 (same pattern as check 12).
        if (
            prev_close is None
            or not math.isfinite(prev_close)
            or prev_close <= 0
        ):
            return ValidationResult(passed=True)

        if stock_meta is not None:
            # Board-aware: compare against the actual exchange-published
            # rounded limits (e.g. prev_close=1.65, 10% board → upper
            # 1.82 = 10.3% raw). A naive raw-percent threshold would
            # reject the published-limit price as "too far" even though
            # the matching engine would accept it. Codex cycle 4 P2.
            limit_pct = self._config.universe.price_limit_pct_by_board.get(
                str(stock_meta.board),
                self._config.position_limits.price_deviation_limit,
            )
            upper = _exchange_price_limit(prev_close, limit_pct, upper=True)
            lower = _exchange_price_limit(prev_close, limit_pct, upper=False)
            if order.price > upper or order.price < lower:
                return ValidationResult(
                    passed=False,
                    rule_name="price_reasonability",
                    message=(
                        f"Price {order.price} outside board "
                        f"'{stock_meta.board}' limits [{lower:.2f}, "
                        f"{upper:.2f}] (prev_close {prev_close})"
                    ),
                )
            return ValidationResult(passed=True)

        # Legacy 7-check fallback: global raw-percent comparison. Keep
        # for backward compat (existing tests without stock_meta) but
        # the builder always supplies stock_meta in production.
        limit = self._config.position_limits.price_deviation_limit
        deviation = abs(order.price - prev_close) / prev_close
        if deviation > limit:
            return ValidationResult(
                passed=False,
                rule_name="price_reasonability",
                message=(
                    f"Price {order.price} deviates {deviation:.1%} "
                    f"from prev_close {prev_close} "
                    f"(limit: +-{limit:.0%}; global fallback)"
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
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
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
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
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
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
        concentration_exception: bool = False,
    ) -> ValidationResult:
        """Check 5: single stock <= max_single_stock_pct of total_assets.

        P0-7-amendment-2026-05-24 §2.4 (方案 A): budget-aware. When the
        proposed position exceeds the 15% limit, a concentration exception
        may be granted — but ONLY for a whitelisted broad ETF at ≤ the
        absolute lot cap, and ONLY when the upstream budget policy flagged
        it. The engine re-derives ETF + whitelist + lot from its own
        ``stock_meta`` + config (``_grant_concentration_exception``), so
        the flag alone is never a single-point bypass. The check count is
        unchanged (still check 5 of 14) — the exception lives inside this
        check, keeping the ``risk_summary`` min=max=14 schema constant.
        """
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
        existing_shares = existing.volume if existing else 0
        proposed_shares = existing_shares + order.volume
        new_value = proposed_shares * order.price
        ratio = new_value / account.total_assets
        limit = self._config.position_limits.max_single_stock_pct

        if ratio > limit:
            if self._grant_concentration_exception(
                order, stock_meta, concentration_exception, proposed_shares
            ):
                return ValidationResult(
                    passed=True,
                    rule_name="position_limit",
                    message=(
                        f"concentration_exception_granted: {order.code} "
                        f"({ratio:.1%} > {limit:.0%}) — whitelisted broad "
                        f"ETF at <= {self._config.concentration_exception.max_lots} "
                        f"lot(s)"
                    ),
                )
            return ValidationResult(
                passed=False,
                rule_name="position_limit",
                message=(
                    f"Position {order.code} would be {ratio:.1%} "
                    f"of portfolio (limit: {limit:.0%})"
                ),
            )
        return ValidationResult(passed=True)

    def _grant_concentration_exception(
        self,
        order: Order,
        stock_meta: StockMetadata | None,
        flag: bool,
        proposed_shares: int,
    ) -> bool:
        """Independently re-validate an over-15% ETF concentration exception.

        Returns True only when ALL hold (defense-in-depth, P0-7-amendment
        §2.3 — never trusts the flag alone):

          * ``flag`` — the budget policy intended an exception (Micro/Small);
          * the gate is ``enabled`` in the (trusted, runtime-immutable) config;
          * ``stock_meta`` is present and ``board == etf`` (fail-closed on
            None — an individual stock never gets the exception);
          * ``order.code`` is in the engine's OWN ``etf_whitelist``
            (re-derived, not taken from the upstream budget policy);
          * the **resulting** position ``proposed_shares`` (existing held +
            this order) ≤ ``max_lots × volume_lot_size``. Capping the
            resulting position, not just this order, stops a flagged 1-lot
            buy from stacking on top of an existing ETF holding to exceed
            the absolute lot cap (codex L-004 P1).
        """
        cfg = self._config.concentration_exception
        if not flag or not cfg.enabled:
            return False
        if stock_meta is None or str(stock_meta.board) != "etf":
            return False
        if order.code not in cfg.etf_whitelist:
            return False
        lot_size = self._config.position_limits.volume_lot_size
        return proposed_shares <= cfg.max_lots * lot_size

    def _check_total_position_limit(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
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
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 7: must be within A-share trading hours."""
        if not is_trading_hours(now):
            return ValidationResult(
                passed=False,
                rule_name="trading_time",
                message="Order rejected: outside trading hours",
            )
        return ValidationResult(passed=True)

    # ------------------------------------------------------------------
    # Checks 8-14 — P0-7 expansion
    # ------------------------------------------------------------------

    def _check_total_position_pct(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 8: total holdings (incl. this order) <= max_total_position_pct.

        SELL trims exposure so always passes. Existing holdings are
        re-valued at the order's limit price for the order's stock and at
        the snapshot ``market_value`` for other positions — matching the
        exposure algorithm used in check 5.
        """
        if order.direction == OrderDirection.SELL:
            return ValidationResult(passed=True)
        if account.total_assets <= 0:
            return ValidationResult(
                passed=False,
                rule_name="total_position_pct",
                message="Cannot trade with zero total assets",
            )

        existing = next(
            (p for p in positions if p.code == order.code), None
        )
        existing_value_for_this = (
            existing.volume * order.price if existing else 0.0
        )
        other_value = sum(
            p.market_value for p in positions if p.code != order.code
        )
        new_order_value = order.volume * order.price
        total_after = existing_value_for_this + other_value + new_order_value
        ratio = total_after / account.total_assets
        limit = self._config.position_limits.max_total_position_pct

        if ratio > limit:
            return ValidationResult(
                passed=False,
                rule_name="total_position_pct",
                message=(
                    f"Total position would be {ratio:.1%} "
                    f"of portfolio (limit: {limit:.0%})"
                ),
            )
        return ValidationResult(passed=True)

    def _check_single_instruction_amount(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 9: single instruction amount <= max_single_instruction_amount.

        Applies to BOTH BUY and SELL — P0-7 §1.1.1 explicitly notes SELL
        is also capped so a single oversized clearing order cannot cause
        a market-impact spike in simulation.
        """
        amount = order.price * order.volume
        limit = self._config.position_limits.max_single_instruction_amount
        if amount > limit:
            return ValidationResult(
                passed=False,
                rule_name="single_instruction_amount",
                message=(
                    f"Instruction amount {amount:.2f} exceeds "
                    f"limit {limit:.2f}"
                ),
            )
        return ValidationResult(passed=True)

    def _check_daily_new_instruction_count(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 10: today's BUY+SELL count strictly below max_daily_new_instructions.

        Backward compat: ``daily_state is None`` → PASS (legacy callers).
        Builder mode: rejects when the count is *already* at the limit so
        the candidate would be the (limit+1)-th.
        """
        if daily_state is None:
            return ValidationResult(passed=True)
        limit = self._config.position_limits.max_daily_new_instructions
        if daily_state.today_new_instruction_count >= limit:
            return ValidationResult(
                passed=False,
                rule_name="daily_new_instruction_count",
                message=(
                    f"Daily new instructions "
                    f"{daily_state.today_new_instruction_count} "
                    f"already at limit {limit}"
                ),
            )
        return ValidationResult(passed=True)

    def _check_universe_whitelist(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 11: board in ``allowed_boards`` AND (if forbidden_st) non-ST.

        Fail-closed on ``stock_meta is None`` (P0-7 §2 redline 13): the
        engine refuses to guess board / ST status from a 6-digit code
        because misclassification would let ST or KCHUANG slip through.
        """
        if stock_meta is None:
            return ValidationResult(
                passed=False,
                rule_name="universe_whitelist",
                message=f"stock_meta unavailable for {order.code}",
            )

        universe = self._config.universe
        board_str = str(stock_meta.board)
        if board_str not in universe.allowed_boards:
            return ValidationResult(
                passed=False,
                rule_name="universe_whitelist",
                message=(
                    f"Board '{board_str}' not in allowed_boards "
                    f"{universe.allowed_boards}"
                ),
            )

        if universe.forbidden_st and stock_meta.is_st:
            return ValidationResult(
                passed=False,
                rule_name="universe_whitelist",
                message=(
                    f"ST stock {order.code} ({stock_meta.name}) forbidden"
                ),
            )

        return ValidationResult(passed=True)

    def _check_limit_up_down_block(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 12: forbid BUY at limit-up / SELL at limit-down.

        Fail-closed on missing ``daily_state`` / ``daily_state.current_price``
        / ``prev_close`` / ``stock_meta`` (P0-7 §2 redline 13). The engine
        intentionally refuses to evaluate limit-up/down without the full
        price-context tuple; check 12's rule_name is reused for both data
        gaps and actual limit hits so audit can distinguish via
        ``message``.
        """
        universe = self._config.universe
        if (
            not universe.forbid_buy_at_limit_up
            and not universe.forbid_sell_at_limit_down
        ):
            return ValidationResult(passed=True)

        # Treat NaN / +-Inf the same as missing data — the quote
        # provider (pandas / akshare) often surfaces missing fields as
        # ``NaN`` rather than ``None``, and the float comparisons below
        # silently return False for ``NaN`` which would let a BUY/SELL
        # through despite the engine being unable to evaluate limit-
        # up/down. Codex cycle 2 P1.
        if (
            daily_state is None
            or daily_state.current_price is None
            or not math.isfinite(daily_state.current_price)
        ):
            return ValidationResult(
                passed=False,
                rule_name="limit_up_down_block",
                message=(
                    "current_price unavailable; "
                    "cannot evaluate limit-up/down"
                ),
            )

        if (
            prev_close is None
            or not math.isfinite(prev_close)
            or prev_close <= 0
        ):
            return ValidationResult(
                passed=False,
                rule_name="limit_up_down_block",
                message=(
                    "prev_close unavailable; "
                    "cannot evaluate limit-up/down"
                ),
            )

        if stock_meta is None:
            return ValidationResult(
                passed=False,
                rule_name="limit_up_down_block",
                message=(
                    "stock_meta unavailable; cannot get board limit_pct"
                ),
            )

        limit_pct = universe.price_limit_pct_by_board.get(
            str(stock_meta.board), 0.10,
        )
        upper_limit = _exchange_price_limit(prev_close, limit_pct, upper=True)
        lower_limit = _exchange_price_limit(prev_close, limit_pct, upper=False)
        current_price = daily_state.current_price

        if (
            order.direction == OrderDirection.BUY
            and universe.forbid_buy_at_limit_up
            and current_price >= upper_limit
        ):
            return ValidationResult(
                passed=False,
                rule_name="limit_up_down_block",
                message=(
                    f"BUY at limit-up forbidden: current {current_price} "
                    f">= upper {upper_limit:.2f}"
                ),
            )

        if (
            order.direction == OrderDirection.SELL
            and universe.forbid_sell_at_limit_down
            and current_price <= lower_limit
        ):
            return ValidationResult(
                passed=False,
                rule_name="limit_up_down_block",
                message=(
                    f"SELL at limit-down forbidden: current {current_price} "
                    f"<= lower {lower_limit:.2f}"
                ),
            )

        return ValidationResult(passed=True)

    def _check_daily_loss_halt(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 13: today's portfolio PnL has not breached the halt threshold.

        SELL bypasses by default (``apply_to_sell_orders=False``) so users
        can still exit during a halt — P0-7 §4.6 prefers an exit ramp
        over a "locked-in" trap.

        Cooldown-only failure (loss recovered but ``is_in_halt_cooldown``
        still true) is also rejected: the halt window must elapse before
        new BUY can re-engage.
        """
        cb = self._config.circuit_breaker
        if daily_state is None:
            return ValidationResult(passed=True)

        if order.direction == OrderDirection.SELL and not cb.apply_to_sell_orders:
            return ValidationResult(passed=True)

        # NaN / +-Inf pnl_pct would silently pass through the strict
        # comparison below. CircuitBreaker.record_trade_result already
        # rejects non-finite values, and check 12 fail-closes NaN
        # quotes — keep check 13 in line. Codex cycle 4 P2.
        if not math.isfinite(daily_state.today_portfolio_pnl_pct):
            return ValidationResult(
                passed=False,
                rule_name="daily_loss_halt",
                message=(
                    f"today_portfolio_pnl_pct non-finite "
                    f"({daily_state.today_portfolio_pnl_pct!r}); "
                    "cannot evaluate halt threshold"
                ),
            )

        threshold = -cb.daily_loss_limit_pct
        # Use inclusive ``<=`` to match CircuitBreaker._should_halt (which
        # trips at exactly ``-daily_loss_limit_pct``). A strict ``<`` would
        # let an order through at the precise -5% boundary even though the
        # breaker would trip on the next price tick. Codex cycle 1 P2.
        if daily_state.today_portfolio_pnl_pct <= threshold:
            return ValidationResult(
                passed=False,
                rule_name="daily_loss_halt",
                message=(
                    f"Daily loss "
                    f"{daily_state.today_portfolio_pnl_pct:.2%} "
                    f"breached halt threshold {threshold:.0%}"
                ),
            )

        if daily_state.is_in_halt_cooldown:
            until = (
                daily_state.halt_until.isoformat()
                if daily_state.halt_until is not None else "unknown"
            )
            return ValidationResult(
                passed=False,
                rule_name="daily_loss_halt",
                message=f"In halt cooldown until {until}",
            )

        return ValidationResult(passed=True)

    def _check_consecutive_loss_halt(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None,
        now: dt.datetime | None,
        daily_state: DailyTradingState | None,
        stock_meta: StockMetadata | None,
    ) -> ValidationResult:
        """Check 14: last N consecutive trade PnLs all negative → reject.

        BUY/SELL both apply (consecutive-loss halt is direction-agnostic,
        unlike check 13). Insufficient history (``len < N``) PASSes — the
        engine cannot evaluate "consecutive N losses" with fewer than N
        samples.
        """
        cb = self._config.circuit_breaker
        if daily_state is None:
            return ValidationResult(passed=True)

        n = cb.consecutive_loss_count
        pnls = daily_state.last_3_trade_pnls
        if len(pnls) < n:
            return ValidationResult(passed=True)
        recent = pnls[-n:]
        if all(pnl < 0 for pnl in recent):
            return ValidationResult(
                passed=False,
                rule_name="consecutive_loss_halt",
                message=(
                    f"Last {n} trades all losing: "
                    f"{[f'{p:.2f}' for p in recent]}"
                ),
            )
        return ValidationResult(passed=True)
