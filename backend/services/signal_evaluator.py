"""Signal accuracy evaluation service."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.data.database import MongoDBService
    from backend.data.history_data import HistoryDataService

log = structlog.get_logger(component="signal_evaluator")


class SignalEvaluator:
    """Evaluate signal accuracy by checking if price moved in predicted direction.

    For each signal:
    - "买入" (Buy): correct if close rose within horizon_days
    - "卖出" (Sell): correct if close fell within horizon_days
    - "持有" (Hold): neutral, excluded from accuracy calc
    """

    def __init__(
        self,
        mongodb: MongoDBService,
        history_data: HistoryDataService,
    ) -> None:
        self._mongodb = mongodb
        self._history = history_data

    async def evaluate(
        self,
        lookback_days: int = 30,
        horizon_days: int = 5,
    ) -> dict[str, Any]:
        """Return accuracy stats: hit_rate, total_evaluated, correct, by_action.

        Args:
            lookback_days: Evaluate signals from last N days.
            horizon_days: Check price movement over next N trading days.

        Returns:
            Dict with hit_rate, total_evaluated, correct, and per-action breakdown.
        """
        signals = await self._mongodb.query_signals(days=lookback_days)

        total = 0
        correct = 0
        by_action: dict[str, dict[str, int]] = {
            "买入": {"total": 0, "correct": 0},
            "卖出": {"total": 0, "correct": 0},
        }

        for signal in signals:
            action = signal.get("action", "")
            if action == "持有":
                continue

            stock_code = signal.get("stock_code", "")
            trade_date = signal.get("trade_date", "")
            if not stock_code or not trade_date:
                continue

            is_correct = await self._check_signal(
                stock_code, trade_date, action, horizon_days
            )
            if is_correct is None:
                continue  # No price data available

            total += 1
            if action in by_action:
                by_action[action]["total"] += 1
            if is_correct:
                correct += 1
                if action in by_action:
                    by_action[action]["correct"] += 1

        hit_rate = round(correct / total, 4) if total > 0 else 0.0

        return {
            "hit_rate": hit_rate,
            "total_evaluated": total,
            "correct": correct,
            "by_action": by_action,
        }

    async def _check_signal(
        self,
        stock_code: str,
        trade_date: str,
        action: str,
        horizon_days: int,
    ) -> bool | None:
        """Check if a signal's prediction was correct.

        Returns True/False for correct/incorrect, None if insufficient data.
        """
        from datetime import date as date_type

        try:
            signal_date = date_type.fromisoformat(trade_date)
            end_date = signal_date + timedelta(days=horizon_days + 5)

            df = await self._history.get_kline(
                stock_code,
                start_date=trade_date,
                end_date=end_date.isoformat(),
            )

            if df is None or df.empty or len(df) < 2:
                return None

            entry_price = float(df.iloc[0]["close"])
            exit_price = float(df.iloc[min(horizon_days, len(df) - 1)]["close"])

            if entry_price == 0:
                return None

            if action == "买入":
                return exit_price > entry_price
            if action == "卖出":
                return exit_price < entry_price

            return None
        except Exception as exc:
            log.warning(
                "signal_check_failed",
                code=stock_code,
                date=trade_date,
                error=str(exc),
            )
            return None
