"""Unit tests for the broker cost calculator (E-003 / P1-2.C)."""

from __future__ import annotations

import pytest

from backend.broker.cost_calculator import (
    TRANSFER_FEE_RATE_SZ,
    OrderCostBreakdown,
    apply_slippage,
    calculate_cost,
)
from backend.broker.models import BrokerConfig, OrderDirection
from backend.data.stock_metadata import Board


@pytest.fixture()
def config() -> BrokerConfig:
    return BrokerConfig()


class TestBoardSlippage:
    """Locked bps map sh_main/sz_main/etf 1.5 + chuangye 3.5 (P1-2.C §1.3)."""

    @pytest.mark.parametrize(
        ("board", "bps_expected"),
        [
            (Board.SH_MAIN, 1.5),
            (Board.SZ_MAIN, 1.5),
            (Board.CHUANGYE, 3.5),
            (Board.ETF, 1.5),
        ],
    )
    def test_buy_slippage_pushes_price_up(
        self, board: Board, bps_expected: float, config: BrokerConfig
    ) -> None:
        price = 100.0
        out = apply_slippage(price, OrderDirection.BUY, board, config)
        expected = round(price * (1 + bps_expected / 10_000), 2)
        assert out == expected
        assert out >= price

    @pytest.mark.parametrize(
        ("board", "bps_expected"),
        [
            (Board.SH_MAIN, 1.5),
            (Board.SZ_MAIN, 1.5),
            (Board.CHUANGYE, 3.5),
            (Board.ETF, 1.5),
        ],
    )
    def test_sell_slippage_pushes_price_down(
        self, board: Board, bps_expected: float, config: BrokerConfig
    ) -> None:
        price = 100.0
        out = apply_slippage(price, OrderDirection.SELL, board, config)
        expected = round(price * (1 - bps_expected / 10_000), 2)
        assert out == expected
        assert out <= price


class TestTransferFee:
    """SZ-side 0.00341% double-sided; SH side untouched."""

    @pytest.mark.parametrize("code", ["000001", "002415", "300750"])
    def test_shenzhen_buy_charges_transfer_fee(
        self, code: str, config: BrokerConfig
    ) -> None:
        board = Board.SZ_MAIN if code.startswith("0") else Board.CHUANGYE
        out = calculate_cost(
            code=code,
            board=board,
            order_price=10.0,
            volume=100,
            direction=OrderDirection.BUY,
            config=config,
        )
        # transfer_fee is 0.00341% of gross
        expected = out.gross_amount * TRANSFER_FEE_RATE_SZ
        assert out.transfer_fee == pytest.approx(expected, abs=0.01)
        assert out.transfer_fee > 0

    @pytest.mark.parametrize("code", ["600519", "601318", "603259"])
    def test_shanghai_buy_charges_no_transfer_fee(
        self, code: str, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code=code,
            board=Board.SH_MAIN,
            order_price=10.0,
            volume=100,
            direction=OrderDirection.BUY,
            config=config,
        )
        assert out.transfer_fee == 0.0

    def test_shanghai_etf_charges_no_transfer_fee(
        self, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code="510300",  # SH ETF
            board=Board.ETF,
            order_price=4.0,
            volume=10_000,
            direction=OrderDirection.BUY,
            config=config,
        )
        assert out.transfer_fee == 0.0

    def test_shenzhen_etf_charges_transfer_fee(
        self, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code="159949",  # SZ ETF
            board=Board.ETF,
            order_price=2.0,
            volume=10_000,
            direction=OrderDirection.BUY,
            config=config,
        )
        assert out.transfer_fee > 0

    def test_sell_also_charges_transfer_fee(
        self, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code="000001",
            board=Board.SZ_MAIN,
            order_price=10.0,
            volume=100,
            direction=OrderDirection.SELL,
            config=config,
        )
        assert out.transfer_fee > 0


class TestNetAmountSemantics:
    """net_amount = cash impact magnitude (always >=0)."""

    def test_buy_net_amount_includes_commission_and_transfer_fee(
        self, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code="000001",
            board=Board.SZ_MAIN,
            order_price=10.0,
            volume=100,
            direction=OrderDirection.BUY,
            config=config,
        )
        expected = round(
            out.gross_amount + out.commission + out.transfer_fee, 2
        )
        assert out.net_amount == expected

    def test_sell_net_amount_subtracts_commission_stamp_transfer(
        self, config: BrokerConfig
    ) -> None:
        out = calculate_cost(
            code="000001",
            board=Board.SZ_MAIN,
            order_price=10.0,
            volume=1_000,
            direction=OrderDirection.SELL,
            config=config,
        )
        expected = round(
            out.gross_amount
            - out.commission
            - out.stamp_tax
            - out.transfer_fee,
            2,
        )
        assert out.net_amount == expected

    def test_min_commission_floor_applied(self, config: BrokerConfig) -> None:
        out = calculate_cost(
            code="600519",
            board=Board.SH_MAIN,
            order_price=1.0,
            volume=100,  # gross 100 CNY * 0.0003 = 0.03 CNY << min 5
            direction=OrderDirection.BUY,
            config=config,
        )
        assert out.commission == 5.0


class TestStrictModel:
    """OrderCostBreakdown is frozen + strict + extra=forbid."""

    def test_breakdown_is_frozen(self, config: BrokerConfig) -> None:
        out = calculate_cost(
            code="600519",
            board=Board.SH_MAIN,
            order_price=100.0,
            volume=100,
            direction=OrderDirection.BUY,
            config=config,
        )
        with pytest.raises(Exception):
            out.commission = 0.0  # type: ignore[misc]

    def test_breakdown_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            OrderCostBreakdown(
                direction=OrderDirection.BUY,
                code="600519",
                board=Board.SH_MAIN,
                order_price=100.0,
                fill_price=100.0,
                volume=100,
                gross_amount=10_000.0,
                commission=5.0,
                stamp_tax=0.0,
                transfer_fee=0.0,
                slippage_cost=0.0,
                net_amount=10_005.0,
                rogue_field="nope",  # type: ignore[call-arg]
            )

    def test_total_friction_sums_components(self) -> None:
        out = OrderCostBreakdown(
            direction=OrderDirection.BUY,
            code="000001",
            board=Board.SZ_MAIN,
            order_price=10.0,
            fill_price=10.0,
            volume=100,
            gross_amount=1_000.0,
            commission=5.0,
            stamp_tax=0.0,
            transfer_fee=0.03,
            slippage_cost=0.15,
            net_amount=1_005.03,
        )
        assert out.total_friction == pytest.approx(5.18)


class TestEdgeCases:
    def test_zero_volume_raises(self, config: BrokerConfig) -> None:
        with pytest.raises(ValueError, match="volume"):
            calculate_cost(
                code="600519",
                board=Board.SH_MAIN,
                order_price=100.0,
                volume=0,
                direction=OrderDirection.BUY,
                config=config,
            )

    def test_zero_price_raises(self, config: BrokerConfig) -> None:
        with pytest.raises(ValueError, match="order_price"):
            calculate_cost(
                code="600519",
                board=Board.SH_MAIN,
                order_price=0.0,
                volume=100,
                direction=OrderDirection.BUY,
                config=config,
            )


class TestBrokerConfigSlippageMap:
    """BrokerConfig.slippage_bps_by_board is sealed + required."""

    def test_loads_default_4_board_table(self) -> None:
        cfg = BrokerConfig()
        for board in ("sh_main", "sz_main", "chuangye", "etf"):
            assert board in cfg.slippage_bps_by_board

    def test_rejects_missing_board(self) -> None:
        with pytest.raises(Exception, match="missing required boards"):
            BrokerConfig(slippage_bps_by_board={"sh_main": 1.5})

    def test_rejects_negative_bps(self) -> None:
        with pytest.raises(Exception, match="non-negative"):
            BrokerConfig(
                slippage_bps_by_board={
                    "sh_main": -0.5,
                    "sz_main": 1.5,
                    "chuangye": 3.5,
                    "etf": 1.5,
                }
            )

    def test_map_is_sealed_after_init(self) -> None:
        cfg = BrokerConfig()
        with pytest.raises((TypeError, AttributeError)):
            cfg.slippage_bps_by_board["sh_main"] = 99.0  # type: ignore[index]

    def test_serializes_to_plain_dict(self) -> None:
        cfg = BrokerConfig()
        dumped = cfg.model_dump(mode="json")
        assert isinstance(dumped["slippage_bps_by_board"], dict)
        assert dumped["slippage_bps_by_board"]["chuangye"] == 3.5
