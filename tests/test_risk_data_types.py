"""Tests for ``backend.risk.daily_state`` and ``backend.risk.stock_meta``.

These data types feed RiskEngine 14-check (checks 10-14 read
``DailyTradingState``; checks 11-12 read ``StockMetadata``). They must
remain frozen value objects — accidental mutation could let a stale halt
flag or board reclassification slip past the 14-check chain.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError, fields
from zoneinfo import ZoneInfo

import pytest

from backend.risk.daily_state import DailyTradingState
from backend.risk.stock_meta import Board, StockMetadata

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# DailyTradingState
# ---------------------------------------------------------------------------


class TestDailyTradingState:
    def _make(self, **overrides: object) -> DailyTradingState:
        defaults: dict[str, object] = {
            "today_new_instruction_count": 2,
            "today_portfolio_pnl_pct": -0.01,
            "last_3_trade_pnls": (10.0, -5.0, 3.0),
            "current_price": 100.0,
            "is_in_halt_cooldown": False,
            "halt_until": None,
        }
        defaults.update(overrides)
        return DailyTradingState(**defaults)  # type: ignore[arg-type]

    def test_basic_construction(self) -> None:
        state = self._make()
        assert state.today_new_instruction_count == 2
        assert state.today_portfolio_pnl_pct == pytest.approx(-0.01)
        assert state.last_3_trade_pnls == (10.0, -5.0, 3.0)
        assert state.current_price == pytest.approx(100.0)
        assert state.is_in_halt_cooldown is False
        assert state.halt_until is None

    def test_is_frozen(self) -> None:
        state = self._make()
        with pytest.raises(FrozenInstanceError):
            state.today_new_instruction_count = 99  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        state = self._make()
        with pytest.raises((AttributeError, TypeError)):
            state.extra_field = "value"  # type: ignore[attr-defined]

    def test_halt_cooldown_with_until(self) -> None:
        until = dt.datetime(2026, 5, 12, 10, 30, tzinfo=SHANGHAI)
        state = self._make(is_in_halt_cooldown=True, halt_until=until)
        assert state.is_in_halt_cooldown is True
        assert state.halt_until == until

    def test_current_price_none_allowed(self) -> None:
        state = self._make(current_price=None)
        assert state.current_price is None

    def test_last_3_trade_pnls_empty_allowed(self) -> None:
        state = self._make(last_3_trade_pnls=())
        assert state.last_3_trade_pnls == ()

    def test_last_3_trade_pnls_is_tuple_immutable(self) -> None:
        state = self._make()
        assert isinstance(state.last_3_trade_pnls, tuple)
        # Tuple itself is immutable; ensure we cannot reassign the field.
        with pytest.raises(FrozenInstanceError):
            state.last_3_trade_pnls = (1.0,)  # type: ignore[misc]

    def test_field_set_matches_doc(self) -> None:
        """Lock down the exact field set so accidental additions break tests."""
        expected = {
            "today_new_instruction_count",
            "today_portfolio_pnl_pct",
            "last_3_trade_pnls",
            "current_price",
            "is_in_halt_cooldown",
            "halt_until",
        }
        assert {f.name for f in fields(DailyTradingState)} == expected


# ---------------------------------------------------------------------------
# Board + StockMetadata
# ---------------------------------------------------------------------------


class TestBoardEnum:
    def test_string_values(self) -> None:
        # Values must be lowercase string identifiers so they match the keys
        # used by ``UniverseConfig.price_limit_pct_by_board`` and
        # ``allowed_boards`` without case-folding.
        assert Board.SH_MAIN.value == "sh_main"
        assert Board.SZ_MAIN.value == "sz_main"
        assert Board.CHUANGYE.value == "chuangye"
        assert Board.KCHUANG.value == "kchuang"
        assert Board.BEIJIAO.value == "beijiao"
        assert Board.ETF.value == "etf"
        assert Board.CONVERTIBLE_BOND.value == "convertible_bond"
        assert Board.UNKNOWN.value == "unknown"

    def test_str_round_trip(self) -> None:
        # ``str(Board.SH_MAIN)`` is what the engine uses to key the
        # universe dict; round-tripping through ``Board(...)`` must
        # return the same member.
        for member in Board:
            assert Board(str(member)) is member

    def test_member_count(self) -> None:
        # Locks down the closed enum to detect accidental additions /
        # removals; new boards require a P0-7 amendment + this test
        # update.
        assert len(Board) == 8


class TestStockMetadata:
    def _make(self, **overrides: object) -> StockMetadata:
        defaults: dict[str, object] = {
            "code": "600519",
            "name": "贵州茅台",
            "board": Board.SH_MAIN,
            "is_st": False,
            "instrument_type": "stock",
        }
        defaults.update(overrides)
        return StockMetadata(**defaults)  # type: ignore[arg-type]

    def test_basic_construction(self) -> None:
        meta = self._make()
        assert meta.code == "600519"
        assert meta.board is Board.SH_MAIN
        assert meta.is_st is False
        assert meta.instrument_type == "stock"

    def test_is_frozen(self) -> None:
        meta = self._make()
        with pytest.raises(FrozenInstanceError):
            meta.board = Board.SZ_MAIN  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        meta = self._make()
        with pytest.raises((AttributeError, TypeError)):
            meta.extra = "x"  # type: ignore[attr-defined]

    def test_st_metadata(self) -> None:
        meta = self._make(name="*ST 西水", is_st=True)
        assert meta.is_st is True

    def test_etf_metadata(self) -> None:
        meta = self._make(
            code="510300", name="沪深300 ETF",
            board=Board.ETF, instrument_type="etf",
        )
        assert meta.board is Board.ETF
        assert meta.instrument_type == "etf"

    def test_field_set_matches_doc(self) -> None:
        expected = {"code", "name", "board", "is_st", "instrument_type"}
        assert {f.name for f in fields(StockMetadata)} == expected


# ---------------------------------------------------------------------------
# UniverseConfig — price_limit_pct_by_board immutability guarantee
# ---------------------------------------------------------------------------


class TestUniverseConfigPriceLimitMapping:
    """P0-7 §2 redline 1: RiskConfig runtime-immutability. Pydantic
    ``frozen=True`` only blocks field reassignment; the dict has to be
    sealed separately or per-key mutation slips past."""

    def test_per_key_mutation_blocked(self) -> None:
        from backend.broker.models import UniverseConfig

        cfg = UniverseConfig()
        with pytest.raises(TypeError):
            cfg.price_limit_pct_by_board["sh_main"] = 0.99  # type: ignore[index]

    def test_pop_blocked(self) -> None:
        from backend.broker.models import UniverseConfig

        cfg = UniverseConfig()
        with pytest.raises((TypeError, AttributeError)):
            cfg.price_limit_pct_by_board.pop("sh_main")  # type: ignore[attr-defined]

    def test_read_still_works(self) -> None:
        from backend.broker.models import UniverseConfig

        cfg = UniverseConfig()
        assert cfg.price_limit_pct_by_board["sh_main"] == pytest.approx(0.10)
        assert cfg.price_limit_pct_by_board["chuangye"] == pytest.approx(0.20)
        assert "etf" in cfg.price_limit_pct_by_board

    def test_field_reassignment_blocked(self) -> None:
        from pydantic import ValidationError

        from backend.broker.models import UniverseConfig

        cfg = UniverseConfig()
        # frozen=True translates dataclass assignment failures into Pydantic
        # ValidationError in v2.
        with pytest.raises(ValidationError):
            cfg.price_limit_pct_by_board = {"sh_main": 0.99}  # type: ignore[misc]

    def test_model_dump_round_trip(self) -> None:
        """MappingProxyType breaks naive Pydantic serialization; the
        custom field_serializer must convert it back to a plain dict
        so ``/api/risk/config`` and ``/api/risk/radar`` can serve JSON.
        Codex cycle 2 P2."""
        import json

        from backend.broker.models import UniverseConfig

        cfg = UniverseConfig()
        dumped = cfg.model_dump(mode="json")
        assert dumped["price_limit_pct_by_board"] == {
            "sh_main": 0.10, "sz_main": 0.10,
            "chuangye": 0.20, "etf": 0.10,
        }
        # End-to-end JSON encode must also succeed.
        encoded = json.dumps(dumped)
        assert "sh_main" in encoded
