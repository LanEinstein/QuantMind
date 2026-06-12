"""AA-003 ops gate tests (P1-2.A-amendment-2026-06-12 §1.4)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from backend.review.ops_gate import (
    ACTIVATION_BLACKOUT,
    DISK_FREE_MIN_BYTES,
    LLM_BUDGET_MIN_REMAINING_CNY,
    OpsGateInputs,
    evaluate_ops_gate,
    next_market_open,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SAT_10 = dt.datetime(2026, 6, 13, 10, 0, tzinfo=SHANGHAI)


def _inputs(**overrides: object) -> OpsGateInputs:
    base: dict[str, object] = {
        "open_ticket_count": 0,
        "snapshot_checksum_valid": True,
        "latest_snapshot_trade_date": "2026-06-12",
        "last_trading_date": "2026-06-12",
        "artifact_registry_ok": True,
        "disk_free_bytes": DISK_FREE_MIN_BYTES * 10,
        "llm_budget_remaining_cny": 100.0,
        "kline_max_date": "2026-06-12",
        "now": SAT_10,
        "next_open_at": dt.datetime(2026, 6, 15, 9, 30, tzinfo=SHANGHAI),
    }
    base.update(overrides)
    return OpsGateInputs(**base)  # type: ignore[arg-type]


class TestEvaluateOpsGate:
    def test_all_green_passes(self) -> None:
        result = evaluate_ops_gate(_inputs())
        assert result.passed
        assert result.failed_names == ()
        assert result.activation_allowed

    def test_open_ticket_fails(self) -> None:
        result = evaluate_ops_gate(_inputs(open_ticket_count=1))
        assert not result.passed
        assert "no_open_reconciliation_ticket" in result.failed_names

    def test_unknown_inputs_fail_closed(self) -> None:
        """Every None observation fails its check (§1.4 fail-closed)."""
        result = evaluate_ops_gate(
            _inputs(
                open_ticket_count=None,
                snapshot_checksum_valid=None,
                latest_snapshot_trade_date=None,
                artifact_registry_ok=None,
                disk_free_bytes=None,
                llm_budget_remaining_cny=None,
                kline_max_date=None,
            )
        )
        assert not result.passed
        assert len(result.failed_names) == 7

    def test_stale_snapshot_fails(self) -> None:
        result = evaluate_ops_gate(
            _inputs(latest_snapshot_trade_date="2026-06-11")
        )
        assert "snapshot_fresh" in result.failed_names

    def test_checksum_invalid_fails(self) -> None:
        result = evaluate_ops_gate(_inputs(snapshot_checksum_valid=False))
        assert "snapshot_checksum_valid" in result.failed_names

    def test_disk_below_threshold_fails(self) -> None:
        result = evaluate_ops_gate(
            _inputs(disk_free_bytes=DISK_FREE_MIN_BYTES - 1)
        )
        assert "disk_free" in result.failed_names

    def test_llm_budget_headroom_boundary(self) -> None:
        ok = evaluate_ops_gate(
            _inputs(llm_budget_remaining_cny=LLM_BUDGET_MIN_REMAINING_CNY)
        )
        assert "llm_budget_headroom" not in ok.failed_names
        low = evaluate_ops_gate(
            _inputs(
                llm_budget_remaining_cny=LLM_BUDGET_MIN_REMAINING_CNY - 0.01
            )
        )
        assert "llm_budget_headroom" in low.failed_names

    def test_stale_kline_fails(self) -> None:
        result = evaluate_ops_gate(_inputs(kline_max_date="2026-06-10"))
        assert "market_data_fresh" in result.failed_names

    def test_activation_blocked_within_2h_of_open(self) -> None:
        near_open = dt.datetime(2026, 6, 15, 8, 0, tzinfo=SHANGHAI)
        result = evaluate_ops_gate(
            _inputs(
                now=near_open,
                next_open_at=dt.datetime(
                    2026, 6, 15, 9, 30, tzinfo=SHANGHAI
                ),
            )
        )
        # The gate itself may pass; only ACTIVATION is blocked.
        assert result.activation_allowed is False

    def test_unknown_next_open_blocks_activation(self) -> None:
        result = evaluate_ops_gate(_inputs(next_open_at=None))
        assert result.activation_allowed is False


class TestNextMarketOpen:
    def test_saturday_resolves_to_monday(self) -> None:
        nxt = next_market_open(SAT_10)
        assert nxt == dt.datetime(2026, 6, 15, 9, 30, tzinfo=SHANGHAI)

    def test_pre_open_same_day(self) -> None:
        monday_8 = dt.datetime(2026, 6, 15, 8, 0, tzinfo=SHANGHAI)
        nxt = next_market_open(monday_8)
        assert nxt == dt.datetime(2026, 6, 15, 9, 30, tzinfo=SHANGHAI)
        assert nxt is not None
        assert nxt - monday_8 < ACTIVATION_BLACKOUT

    def test_post_open_rolls_to_next_session(self) -> None:
        monday_11 = dt.datetime(2026, 6, 15, 11, 0, tzinfo=SHANGHAI)
        nxt = next_market_open(monday_11)
        assert nxt == dt.datetime(2026, 6, 16, 9, 30, tzinfo=SHANGHAI)

    def test_no_trading_day_returns_none(self) -> None:
        assert (
            next_market_open(SAT_10, is_trading_day_fn=lambda _d: False)
            is None
        )
