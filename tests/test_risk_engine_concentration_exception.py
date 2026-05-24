"""L-004 tests: RiskEngine check-5 budget-aware ETF concentration exception.

P0-7-amendment-2026-05-24 §2.4 (方案 A): the single-stock check may grant
an over-15% exception, but ONLY for a whitelisted broad ETF at ≤1 lot AND
only when the upstream budget policy flagged it; the engine re-derives the
eligibility from its own config + stock_meta (never the flag alone). The
14-check count is unchanged (the exception lives inside check 5).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import (
    AccountInfo,
    Order,
    OrderDirection,
    OrderType,
    RiskConfig,
    load_risk_config,
)
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board, StockMetadata

SHANGHAI = ZoneInfo("Asia/Shanghai")

RISK_YAML_CE = """\
position_limits:
  max_single_stock_pct: 0.15
  max_sector_pct: 0.40
  max_total_positions: 10
  price_deviation_limit: 0.05
  volume_lot_size: 100
  max_total_position_pct: 0.70
  max_single_instruction_amount: 50000
  max_daily_new_instructions: 5
stop_loss:
  single_stock_pct: 0.08
  portfolio_daily_pct: 0.05
  trailing_stop_pct: 0.10
circuit_breaker:
  daily_loss_limit_pct: 0.05
  consecutive_loss_count: 3
  cooldown_minutes: 60
  halt_priority_order: ["daily_loss", "consecutive_loss"]
  apply_to_sell_orders: false
universe:
  allowed_boards: ["sh_main", "sz_main", "chuangye", "etf"]
  forbidden_st: true
  forbid_buy_at_limit_up: true
  forbid_sell_at_limit_down: true
  price_limit_pct_by_board:
    sh_main: 0.10
    sz_main: 0.10
    chuangye: 0.20
    etf: 0.10
concentration_exception:
  enabled: true
  etf_whitelist: ["510300", "510500", "159949"]
  max_lots: 1
"""


@pytest.fixture()
def cfg(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk_ce.yaml"
    path.write_text(RISK_YAML_CE, encoding="utf-8")
    return load_risk_config(path)


def _now() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)  # Mon 10:00


def _order(
    code: str = "510300",
    price: float = 4.0,
    volume: int = 100,
    direction: OrderDirection = OrderDirection.BUY,
) -> Order:
    return Order(
        order_id="t", code=code, price=price, volume=volume,
        direction=direction, order_type=OrderType.LIMIT,
        created_at=_now(), updated_at=_now(),
    )


def _account(total_assets: float) -> AccountInfo:
    return AccountInfo(
        total_assets=total_assets, available_cash=total_assets,
        frozen_cash=0.0, market_value=0.0,
        total_pnl=0.0, total_pnl_pct=0.0, initial_capital=total_assets,
    )


def _meta(code: str = "510300", board: Board = Board.ETF) -> StockMetadata:
    return StockMetadata(
        code=code, name=code, board=board, is_st=False,
        instrument_type="etf" if board is Board.ETF else "stock",
    )


def _state() -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=4.0,
        is_in_halt_cooldown=False, halt_until=None,
    )


def _validate(cfg: RiskConfig, order: Order, account: AccountInfo, *,
              meta: StockMetadata | None, flag: bool):
    return RiskEngine(cfg).validate_order(
        order, account, (), prev_close=4.0, now=_now(),
        daily_state=_state() if meta is not None else None,
        stock_meta=meta, concentration_exception=flag,
    )


class TestConcentrationException:
    @pytest.mark.unit
    def test_etf_exception_granted(self, cfg: RiskConfig) -> None:
        # 1-lot ETF ¥400 on ¥2,000 = 20% > 15%; whitelisted + 1 lot + flag.
        # The grant flips check 5 from fail to pass AND validate_order
        # surfaces the concentration_exception_granted marker (P2) for audit.
        r = _validate(cfg, _order(), _account(2000.0), meta=_meta(), flag=True)
        assert r.passed
        assert r.rule_name == "position_limit"
        assert "concentration_exception_granted" in r.message

    @pytest.mark.unit
    def test_existing_position_stacking_over_lot_cap_rejected(
        self, cfg: RiskConfig
    ) -> None:
        # Holding 1 lot of 510300 and buying another lot leaves a 2-lot
        # resulting position — over the absolute 1-lot exception cap, so the
        # flagged buy is rejected (codex L-004 P1: cap the resulting
        # position, not just this order).
        from backend.broker.models import Position

        held = Position(
            code="510300", volume=100, available_volume=100,
            cost_price=4.0, market_value=400.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        r = RiskEngine(cfg).validate_order(
            _order(volume=100), _account(2000.0), (held,),
            prev_close=4.0, now=_now(), daily_state=_state(),
            stock_meta=_meta(), concentration_exception=True,
        )
        assert not r.passed
        assert r.rule_name == "position_limit"

    @pytest.mark.unit
    def test_grant_surfaces_annotation_at_check_level(self, cfg: RiskConfig) -> None:
        # The per-check result (collected into the 14-entry RiskCheckSummary
        # by the builder) carries the concentration_exception_granted reason.
        engine = RiskEngine(cfg)
        r = engine._check_position_limit(
            _order(), _account(2000.0), (), 4.0, _now(), _state(), _meta(),
            True,
        )
        assert r.passed
        assert r.rule_name == "position_limit"
        assert "concentration_exception_granted" in r.message

    @pytest.mark.unit
    def test_no_exception_without_flag(self, cfg: RiskConfig) -> None:
        # Same over-15% ETF but the budget policy did NOT flag it → rejected.
        r = _validate(cfg, _order(), _account(2000.0), meta=_meta(), flag=False)
        assert not r.passed
        assert r.rule_name == "position_limit"
        assert "concentration_exception_granted" not in r.message

    @pytest.mark.unit
    def test_individual_stock_never_gets_exception(self, cfg: RiskConfig) -> None:
        # A 600-prefixed stock over 15% with the flag set is still rejected:
        # the exception is ETF-only (个股不享有).
        r = _validate(
            cfg,
            _order(code="600519"),
            _account(2000.0),
            meta=_meta(code="600519", board=Board.SH_MAIN),
            flag=True,
        )
        assert not r.passed
        assert r.rule_name == "position_limit"
        assert "concentration_exception_granted" not in r.message

    @pytest.mark.unit
    def test_over_one_lot_rejected(self, cfg: RiskConfig) -> None:
        # 2 lots (¥800) on ¥4,000 = 20% > 15%; whitelisted + flag, but
        # volume 200 > absolute 1-lot cap → exception denied.
        r = _validate(
            cfg, _order(volume=200), _account(4000.0), meta=_meta(), flag=True
        )
        assert not r.passed
        assert r.rule_name == "position_limit"

    @pytest.mark.unit
    def test_non_whitelisted_etf_rejected(self, cfg: RiskConfig) -> None:
        # An ETF board code NOT in the engine's whitelist gets no exception
        # even with the flag (independent re-validation against own config).
        r = _validate(
            cfg,
            _order(code="510999"),
            _account(2000.0),
            meta=_meta(code="510999", board=Board.ETF),
            flag=True,
        )
        assert not r.passed
        assert r.rule_name == "position_limit"

    @pytest.mark.unit
    def test_under_15pct_passes_without_exception(self, cfg: RiskConfig) -> None:
        # ¥400 on ¥10,000 = 4% < 15% → normal pass, no exception machinery.
        r = _validate(cfg, _order(), _account(10000.0), meta=_meta(), flag=False)
        assert r.passed
        assert "concentration_exception_granted" not in r.message

    @pytest.mark.unit
    def test_missing_stock_meta_fails_closed(self, cfg: RiskConfig) -> None:
        # No stock_meta (legacy mode): the engine cannot verify board==etf,
        # so the exception is denied fail-closed even with the flag.
        r = _validate(cfg, _order(), _account(2000.0), meta=None, flag=True)
        assert not r.passed
        assert r.rule_name == "position_limit"

    @pytest.mark.unit
    def test_disabled_gate_denies_exception(self, tmp_path: Path) -> None:
        yaml_text = RISK_YAML_CE.replace("enabled: true", "enabled: false")
        path = tmp_path / "off.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        cfg = load_risk_config(path)
        r = _validate(cfg, _order(), _account(2000.0), meta=_meta(), flag=True)
        assert not r.passed


class TestConfigAndCheckCount:
    @pytest.mark.unit
    def test_default_concentration_exception_config(self, tmp_path: Path) -> None:
        # A risk.yaml without the section still loads (default factory).
        minimal = "\n".join(
            line
            for line in RISK_YAML_CE.splitlines()
            if not line.startswith("concentration_exception")
            and "etf_whitelist:" not in line
            and "max_lots:" not in line
            and "enabled:" not in line
        )
        path = tmp_path / "min.yaml"
        path.write_text(minimal, encoding="utf-8")
        cfg = load_risk_config(path)
        assert cfg.concentration_exception.enabled is True
        assert cfg.concentration_exception.max_lots == 1
        assert "510300" in cfg.concentration_exception.etf_whitelist

    @pytest.mark.unit
    def test_risk_summary_still_14(self) -> None:
        # 方案 A: the exception lives inside check 5 — InstructionPlan's
        # risk_summary schema constant must stay min=max=14 (no new check).
        from backend.models.instruction import InstructionPlan

        meta = InstructionPlan.model_fields["risk_summary"].metadata
        mins = [getattr(m, "min_length", None) for m in meta]
        maxs = [getattr(m, "max_length", None) for m in meta]
        assert 14 in mins, f"risk_summary min_length must be 14, metadata={meta}"
        assert 14 in maxs, f"risk_summary max_length must be 14, metadata={meta}"
