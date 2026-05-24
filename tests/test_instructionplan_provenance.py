"""M-004 — InstructionPlan single-construction-point + field provenance.

R0 §4 red line B: the InstructionPlan is constructed in exactly one place
(``instruction_plan_builder``), and its numeric order fields
(``side`` / ``volume`` / ``limit_price``) are derived deterministically from
non-LLM inputs — NEVER parsed from the LLM-writable ``proposal_text`` (or any
of the four LLM-writable text fields). Import isolation cannot prove field
provenance, so this module is the adversarial half of the boundary:

* feed a numeric / direction-laden ``proposal_text`` and assert the order
  numbers come from the AssemblyContext position-sizer, not the text;
* assert the LLM text never lands on the constructed plan at all;
* a static scan that ``InstructionPlan(`` is named only in the model + the
  builder (mirrors the ``[M-004]`` redline-check sub-check, enforced here too).
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.data.data_quality import DataQualityState
from backend.models.instruction import DataSnapshot, InstructionSide, InstructionStatus
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.services.fund_manager_output import FundManagerOutput
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    BuilderPlan,
    InstructionPlanBuilder,
    MandatoryAgentRecords,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import load_policy

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAPSHOT_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_CODE = "510300"
_NAME = "沪深300 ETF"

# A deterministic position-sizer's order numbers — the only legitimate source
# of side/volume/limit_price.
_SIZED_VOLUME = 200
_SIZED_PRICE = 4.5

# Numbers smuggled inside the LLM proposal_text that must NEVER reach the plan.
_ADVERSARIAL_VOLUME = 5000
_ADVERSARIAL_PRICE = 999.0


@pytest.fixture
def audit_store(tmp_path: Path) -> AuditStore:
    return AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl")


@pytest.fixture
def builder(audit_store: AuditStore) -> InstructionPlanBuilder:
    return InstructionPlanBuilder(audit_store=audit_store)


def _passing_context() -> AssemblyContext:
    """A context that lets a 200-lot 510300 BUY at 4.5 pass the 14-check."""
    return AssemblyContext(
        stock_code=_CODE,
        stock_name=_NAME,
        now=_NOW,
        open_tickets=(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        data_quality=DataQualityState(
            quote_unavailable=False,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=2,
            backup_quote_age_seconds=2,
            news_sources_alive_count=5,
        ),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        watchlist_signal=WatchlistMarketSignal(
            listed_at_trading_days=720,
            avg_amount_20d_yuan=1_000_000_000.0,
            last_price_yuan=_SIZED_PRICE,
        ),
        risk_engine=RiskEngine(
            RiskConfig(
                position_limits=PositionLimitsConfig(),
                stop_loss=StopLossConfig(),
                circuit_breaker=CircuitBreakerConfig(),
                universe=UniverseConfig(),
            )
        ),
        account=AccountInfo(
            total_assets=1_000_000.0,
            available_cash=900_000.0,
            frozen_cash=0.0,
            market_value=100_000.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=1_000_000.0,
        ),
        positions=(),
        prev_close=_SIZED_PRICE,
        daily_state=DailyTradingState(
            today_new_instruction_count=0,
            today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(),
            current_price=_SIZED_PRICE,
            is_in_halt_cooldown=False,
            halt_until=None,
        ),
        stock_meta=RiskStockMetadata(
            code=_CODE, name=_NAME, board=RiskBoard.ETF, is_st=False,
            instrument_type="etf",
        ),
        proposed_volume=_SIZED_VOLUME,
        proposed_limit_price=_SIZED_PRICE,
        seq=1,
        signal_id="sig-2026-05-15-001",
        analysis_record_id="ar-2026-05-15-001",
        risk_validation_id="rv-2026-05-15-001",
        debate_round_count=1,
        evidence_ids=(),
        data_snapshot=DataSnapshot(
            snapshot_at=_SNAPSHOT_AT,
            quote_source="primary",
            quote_latency_ms=200,
            is_trading_day=True,
            is_trading_hours=True,
            prev_close=_SIZED_PRICE,
        ),
        invalidation_summary="default invalidation summary",
    )


def _all_records() -> MandatoryAgentRecords:
    return MandatoryAgentRecords(
        fundamental_analyst_record_id="step-fa-1",
        technical_analyst_record_id="step-ta-1",
        risk_officer_record_id="step-ro-1",
        fund_manager_record_id="step-fm-1",
    )


# Adversarial proposal_text: numbers + opposite-direction words the LLM might
# use to try to steer the order. None of this may reach the plan's numerics.
_ADVERSARIAL_TEXT = (
    f"BUY {_ADVERSARIAL_VOLUME} shares at limit {_ADVERSARIAL_PRICE} — "
    "SELL SELL SELL everything NOW volume=999999 price=12345"
)


# --------------------------------------------------------------------------
# Field provenance — numbers/directions in proposal_text never reach the plan
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_numeric_proposal_text_does_not_set_volume_or_price(
    builder: InstructionPlanBuilder,
) -> None:
    fmo = FundManagerOutput(
        side=InstructionSide.BUY, proposal_text=_ADVERSARIAL_TEXT, parse_ok=True
    )
    result = await builder.assemble_plan(
        fund_manager_output=fmo,
        mandatory_records=_all_records(),
        context=_passing_context(),
    )
    assert isinstance(result, BuilderPlan)
    plan = result.plan
    # Numerics come from the position-sizer (AssemblyContext), not the text.
    assert plan.volume == _SIZED_VOLUME
    assert plan.limit_price == _SIZED_PRICE
    assert plan.volume != _ADVERSARIAL_VOLUME
    assert plan.limit_price != _ADVERSARIAL_PRICE


@pytest.mark.asyncio
async def test_proposal_text_direction_words_do_not_override_side(
    builder: InstructionPlanBuilder,
) -> None:
    """side comes from the typed FundManagerOutput.side enum, never from the
    'SELL SELL SELL' words embedded in the free text."""
    fmo = FundManagerOutput(
        side=InstructionSide.BUY, proposal_text=_ADVERSARIAL_TEXT, parse_ok=True
    )
    result = await builder.assemble_plan(
        fund_manager_output=fmo,
        mandatory_records=_all_records(),
        context=_passing_context(),
    )
    assert isinstance(result, BuilderPlan)
    assert result.plan.side is InstructionSide.BUY


@pytest.mark.asyncio
async def test_proposal_text_never_appears_on_the_plan(
    builder: InstructionPlanBuilder,
) -> None:
    """The LLM-writable text is consumed only to pick side; it must not be
    persisted into any InstructionPlan field (no reasoning/text leak)."""
    fmo = FundManagerOutput(
        side=InstructionSide.BUY, proposal_text=_ADVERSARIAL_TEXT, parse_ok=True
    )
    result = await builder.assemble_plan(
        fund_manager_output=fmo,
        mandatory_records=_all_records(),
        context=_passing_context(),
    )
    assert isinstance(result, BuilderPlan)
    dumped = result.plan.model_dump()
    for value in dumped.values():
        if isinstance(value, str):
            assert _ADVERSARIAL_TEXT not in value
            assert str(_ADVERSARIAL_VOLUME) not in value
            assert "12345" not in value


@pytest.mark.asyncio
async def test_parse_ok_false_forces_hold_regardless_of_buy_side(
    builder: InstructionPlanBuilder,
) -> None:
    """Even side=BUY with parse_ok=False yields a HOLD plan (no volume/price)
    — a malformed LLM envelope can never produce a tradable order
    (P0-3 §2 redline 6)."""
    fmo = FundManagerOutput(
        side=InstructionSide.BUY, proposal_text="(synthetic)", parse_ok=False
    )
    result = await builder.assemble_plan(
        fund_manager_output=fmo,
        mandatory_records=_all_records(),
        context=_passing_context(),
    )
    assert isinstance(result, BuilderPlan)
    assert result.plan.side is InstructionSide.HOLD
    assert result.plan.volume is None
    assert result.plan.limit_price is None
    assert result.plan.status is InstructionStatus.VALIDATED


# --------------------------------------------------------------------------
# Single construction point — static scan over backend/ (mirrors redline-check)
# --------------------------------------------------------------------------

_ALLOWED_CONSTRUCTION_SITES = {
    Path("backend/models/instruction.py"),
    Path("backend/services/instruction_plan_builder.py"),
}


def _construction_sites(root: Path = Path("backend")) -> set[Path]:
    """Files under ``root`` that construct ``InstructionPlan`` (AST, alias-aware).

    Robust to comments/strings that merely mention the name, and to evasion via
    an aliased import (``import InstructionPlan as Plan; Plan(...)``) or an
    attribute call (``module.InstructionPlan(...)``) — mirrors the [M-004]
    redline-check scanner (codex M-004 P2)."""
    sites: set[Path] = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover — defensive
            continue
        names: set[str] = set()
        for node in ast.walk(tree):
            # Seed from ANY ``from ... import InstructionPlan [as X]`` so a
            # re-export alias (``from backend.models import InstructionPlan``)
            # is caught too (codex M-004 verify finding). Only one such class
            # exists, so matching the imported name is safe.
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "InstructionPlan":
                        names.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Name) and func.id in names) or (
                isinstance(func, ast.Attribute) and func.attr == "InstructionPlan"
            ):
                sites.add(py)
                break
    return sites


def test_instructionplan_constructed_only_in_model_and_builder() -> None:
    sites = _construction_sites()
    extra = sites - _ALLOWED_CONSTRUCTION_SITES
    assert extra == set(), (
        f"InstructionPlan constructed outside the single construction point: "
        f"{sorted(map(str, extra))}"
    )


def test_builder_is_the_only_service_construction_site() -> None:
    """The builder must remain a construction site (guards against an
    accidental refactor that moves construction out of it)."""
    assert Path("backend/services/instruction_plan_builder.py") in _construction_sites()


def test_scanner_flags_aliased_import_construction(tmp_path: Path) -> None:
    """The scanner is not foolable by an aliased import (codex M-004 P2):
    ``from backend.models.instruction import InstructionPlan as Plan; Plan(...)``
    must be detected even though a raw 'InstructionPlan(' grep would miss it."""
    rogue = tmp_path / "rogue_alias.py"
    rogue.write_text(
        "from backend.models.instruction import InstructionPlan as Plan\n"
        "def make():\n    return Plan(stock_code='510300')\n",
        encoding="utf-8",
    )
    assert rogue in _construction_sites(tmp_path)


def test_scanner_flags_reexport_alias_construction(tmp_path: Path) -> None:
    """The scanner catches construction via the ``backend.models`` re-export
    (``from backend.models import InstructionPlan as Plan; Plan(...)``) — the
    package __init__ re-exports the class, so a module-suffix filter would miss
    it (codex M-004 verify finding)."""
    rogue = tmp_path / "rogue_reexport.py"
    rogue.write_text(
        "from backend.models import InstructionPlan as Plan\n"
        "def make():\n    return Plan(stock_code='510300')\n",
        encoding="utf-8",
    )
    assert rogue in _construction_sites(tmp_path)


def test_scanner_flags_attribute_construction(tmp_path: Path) -> None:
    """Attribute-style construction (``module.InstructionPlan(...)``) is also
    detected (codex M-004 P2)."""
    rogue = tmp_path / "rogue_attr.py"
    rogue.write_text(
        "import backend.models.instruction as m\n"
        "def make():\n    return m.InstructionPlan(stock_code='510300')\n",
        encoding="utf-8",
    )
    assert rogue in _construction_sites(tmp_path)


def test_scanner_no_false_positive_on_mention_only(tmp_path: Path) -> None:
    """A file that merely mentions InstructionPlan in a string/comment without
    constructing it is NOT flagged."""
    benign = tmp_path / "mention_only.py"
    benign.write_text(
        '"""Talks about InstructionPlan() in a docstring."""\n'
        "# InstructionPlan( is referenced in this comment\n"
        "X = 'InstructionPlan('\n",
        encoding="utf-8",
    )
    assert benign not in _construction_sites(tmp_path)
