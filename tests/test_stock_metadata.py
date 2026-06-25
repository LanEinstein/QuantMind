"""C-001 — stock_metadata pure-module tests.

Coverage targets per ``docs/plan.html`` C-001:
* prefix table (allowed boards) — table-driven across SH/SZ/ChiNext/ETF.
* forbidden prefixes raise ``ForbiddenCodeError`` with a stable
  ``reason`` namespace ready for audit.
* malformed / unknown codes raise ``UnknownCodeError``.
* ST detection by name (ST / *ST / 退 / PT, prefix or embedded).
* ``get_price_limit_pct`` single source: 10% main+ETF, 20% ChiNext.
* ``get_price_limits`` rounds to 2dp + treats ``prev_close ≤ 0`` as
  ``(0.0, 0.0)`` (data_unavailable, not error).
* ``StockMetadata`` is frozen + strict + ``extra='forbid'``.
* No-LLM-import redline: stock_metadata never pulls
  ``backend.{llm,agents,mirofish}``.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import date

import pytest
from pydantic import ValidationError

from backend.data import stock_metadata as sm
from backend.data.stock_metadata import (
    Board,
    ForbiddenCodeError,
    StockMetadata,
    UnknownCodeError,
    build_metadata,
    classify_board,
    get_lot_size,
    get_price_limit_pct,
    get_price_limits,
    is_st_name,
)

# ---------------------------------------------------------------------------
# Allowed-board prefix table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # SH main
        ("600519", Board.SH_MAIN),  # 贵州茅台
        ("601318", Board.SH_MAIN),  # 中国平安
        ("603288", Board.SH_MAIN),  # 海天味业
        ("605499", Board.SH_MAIN),  # 东鹏饮料
        # SZ main
        ("000001", Board.SZ_MAIN),  # 平安银行
        ("000858", Board.SZ_MAIN),  # 五粮液
        ("001979", Board.SZ_MAIN),  # 招商蛇口
        ("002594", Board.SZ_MAIN),  # 比亚迪
        ("003816", Board.SZ_MAIN),  # 中国广核
        # ChiNext
        ("300750", Board.CHUANGYE),  # 宁德时代
        ("300059", Board.CHUANGYE),  # 东方财富
        ("301059", Board.CHUANGYE),  # 金阳新能源
        # ETFs (P0-9 mandatory triplet + STAR-ETF wrapper)
        ("510300", Board.ETF),  # 沪深300
        ("510500", Board.ETF),  # 中证500
        ("159949", Board.ETF),  # 创业板50
        ("588000", Board.ETF),  # 科创50 ETF — wrapper allowed
        ("512880", Board.ETF),  # 证券 ETF
    ],
)
def test_classify_board_allowed(code: str, expected: Board) -> None:
    assert classify_board(code) is expected


# ---------------------------------------------------------------------------
# Forbidden boards / securities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        # STAR market raw individual stocks — P0-9 §1.2 forbidden
        ("688981", "star_forbidden"),
        ("689009", "star_forbidden"),
        # 北交所 — broad prefix match per docstring promise (4xx/8xx/920).
        # Mix legacy NEEQ codes + post-reform BJ codes + edge prefixes
        # outside the original enumeration to prove fail-closed coverage.
        ("430047", "bj_forbidden"),
        ("499999", "bj_forbidden"),  # any 4-prefix → BJ namespace
        ("830799", "bj_forbidden"),
        ("831010", "bj_forbidden"),
        ("872925", "bj_forbidden"),
        ("880001", "bj_forbidden"),  # NEEQ basic-layer 8-prefix
        ("899999", "bj_forbidden"),  # any 8-prefix not in allowed set
        ("920099", "bj_forbidden"),
        # 可转债
        ("110085", "cb_forbidden"),
        ("113537", "cb_forbidden"),
        ("123073", "cb_forbidden"),
        ("127061", "cb_forbidden"),
        ("128042", "cb_forbidden"),
        ("132018", "cb_forbidden"),
        # B-shares
        ("200002", "b_share_forbidden"),
        ("900907", "b_share_forbidden"),
    ],
)
def test_classify_board_forbidden(code: str, reason: str) -> None:
    with pytest.raises(ForbiddenCodeError) as exc:
        classify_board(code)
    assert exc.value.code == code
    assert exc.value.reason == reason
    assert reason in str(exc.value)


# ---------------------------------------------------------------------------
# Malformed / unknown codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["", "60051", "6005199", "60051a", "abcdef", "999999", "100000"],
)
def test_classify_board_unknown(code: str) -> None:
    with pytest.raises(UnknownCodeError):
        classify_board(code)


def test_classify_board_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        classify_board(600519)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ST detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("贵州茅台", False),
        ("ST 中安", True),
        ("*ST 中安", True),
        ("st 中安", True),  # case-insensitive
        ("中视退", True),
        ("PT 农商行", True),
        ("中视PT", True),
        ("", False),
    ],
)
def test_is_st_name(name: str, expected: bool) -> None:
    assert is_st_name(name) is expected


# ---------------------------------------------------------------------------
# Price-limit single source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("board", "expected"),
    [
        (Board.SH_MAIN, 0.10),
        (Board.SZ_MAIN, 0.10),
        (Board.ETF, 0.10),
        (Board.CHUANGYE, 0.20),
    ],
)
def test_get_price_limit_pct(board: Board, expected: float) -> None:
    assert get_price_limit_pct(board) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("board", "prev_close", "expected_low", "expected_high"),
    [
        (Board.SH_MAIN, 100.0, 90.00, 110.00),
        (Board.SZ_MAIN, 100.0, 90.00, 110.00),
        (Board.ETF, 4.50, 4.05, 4.95),
        (Board.CHUANGYE, 100.0, 80.00, 120.00),
        (Board.CHUANGYE, 9.99, 7.99, 11.99),  # rounding
    ],
)
def test_get_price_limits(
    board: Board, prev_close: float, expected_low: float, expected_high: float
) -> None:
    low, high = get_price_limits(board, prev_close)
    assert low == pytest.approx(expected_low)
    assert high == pytest.approx(expected_high)


@pytest.mark.parametrize("prev_close", [0.0, -1.0, -100.0])
def test_get_price_limits_returns_zeros_for_nonpositive_prev_close(
    prev_close: float,
) -> None:
    assert get_price_limits(Board.SH_MAIN, prev_close) == (0.0, 0.0)


def test_round_half_up_diverges_from_pythons_banker_round() -> None:
    """Regression test for codex-review cycle 1 P2 (2026-05-12).

    A-share exchanges round 涨跌停 prices half-up (四舍五入), not banker's
    half-even. Python's built-in ``round(7.255, 2)`` returns ``7.25``
    because the IEEE 754 value is just below 7.255; our helper goes via
    ``Decimal(str(value))`` so the human-typed half lands as 7.26.
    """
    from backend.data.stock_metadata import _round_half_up

    assert round(7.255, 2) == 7.25  # baseline: banker's behaviour
    assert _round_half_up(7.255) == 7.26  # exchange-correct HALF_UP
    assert _round_half_up(0.005) == 0.01
    assert _round_half_up(0.015) == 0.02


def test_get_price_limits_uses_decimal_arithmetic_end_to_end() -> None:
    """Regression test for codex-review cycle 2 NOT_RESOLVED (2026-05-12).

    Doing ``float * float`` before Decimal conversion lets IEEE 754 bias
    the half: for ``prev_close=1.65`` (stored as 1.6499999…) the
    SH-main lower limit must equal ``1.49`` (math: 1.65 × 0.9 = 1.485,
    HALF_UP → 1.49). The previous implementation rounded 1.4849999… and
    returned ``1.48``. Decimal-first arithmetic restores 1.49.
    """
    low, high = get_price_limits(Board.SH_MAIN, 1.65)
    assert low == 1.49
    assert high == 1.82  # 1.65 * 1.1 = 1.815, HALF_UP → 1.82


@pytest.mark.parametrize(
    "board", [Board.SH_MAIN, Board.SZ_MAIN, Board.CHUANGYE, Board.ETF]
)
def test_at_fill_recheck_parity_with_risk_engine(board: Board) -> None:
    """B8 (production-hardening 2026-06-25): MockBroker's at-fill price-limit
    recheck now uses ``get_price_limits`` (Decimal HALF_UP). It must equal
    RiskEngine's independent ``_exchange_price_limit`` (also Decimal HALF_UP)
    across the whole price range — including the ``.xx5``-cent boundaries where
    MockBroker's prior ``round()`` (banker's HALF_EVEN) diverged and produced
    spurious at-fill rejects. RiskEngine keeps its own copy of the formula
    because backend/risk must not import backend.data, so this is the parity
    guard that keeps the two in lock-step.
    """
    from backend.data.stock_metadata import get_price_limit_pct
    from backend.risk.engine import _exchange_price_limit

    pct = get_price_limit_pct(board)
    for cents in range(1, 20_001):  # 0.01 .. 200.00, every cent
        prev_close = cents / 100.0
        low, high = get_price_limits(board, prev_close)
        assert high == _exchange_price_limit(prev_close, pct, upper=True), (
            board, prev_close
        )
        assert low == _exchange_price_limit(prev_close, pct, upper=False), (
            board, prev_close
        )


def test_price_limit_pct_constants_match_risk_config() -> None:
    """B8 drift guard (codex review): the parity above feeds RiskEngine's
    ``_exchange_price_limit`` the hardcoded ``get_price_limit_pct`` constant,
    but at runtime RiskEngine reads pct from ``risk.yaml``'s
    ``universe.price_limit_pct_by_board``. They agree today; this asserts they
    STAY in lock-step so a future risk.yaml edit that diverges from the
    stock_metadata constants fails CLOSED here instead of silently passing the
    parity test (and silently diverging MockBroker's at-fill recheck from
    RiskEngine's limit-up block).
    """
    from backend.broker.models import load_risk_config
    from backend.data.stock_metadata import get_price_limit_pct

    cfg = load_risk_config("config/risk.yaml")
    by_board = cfg.universe.price_limit_pct_by_board
    for board in (Board.SH_MAIN, Board.SZ_MAIN, Board.CHUANGYE, Board.ETF):
        assert by_board[str(board)] == get_price_limit_pct(board), str(board)


# ---------------------------------------------------------------------------
# Lot size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("board", list(Board))
def test_get_lot_size_is_100_across_boards(board: Board) -> None:
    assert get_lot_size(board) == 100


# ---------------------------------------------------------------------------
# StockMetadata DTO — frozen + strict + extra=forbid
# ---------------------------------------------------------------------------


def test_stock_metadata_round_trip() -> None:
    md = StockMetadata(
        code="600519",
        name="贵州茅台",
        board=Board.SH_MAIN,
        price_limit_pct=0.10,
        listed_date=date(2001, 8, 27),
    )
    assert md.code == "600519"
    assert md.lot_size == 100  # default
    assert md.is_st is False  # default


def test_stock_metadata_is_frozen() -> None:
    md = StockMetadata(
        code="600519",
        name="贵州茅台",
        board=Board.SH_MAIN,
        price_limit_pct=0.10,
    )
    with pytest.raises(ValidationError):
        md.code = "000001"  # type: ignore[misc]


def test_stock_metadata_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        StockMetadata(
            code="600519",
            name="贵州茅台",
            board=Board.SH_MAIN,
            price_limit_pct=0.10,
            unexpected_field="x",  # type: ignore[call-arg]
        )


def test_stock_metadata_rejects_bad_code() -> None:
    with pytest.raises(ValidationError):
        StockMetadata(
            code="60051",
            name="贵州茅台",
            board=Board.SH_MAIN,
            price_limit_pct=0.10,
        )


def test_stock_metadata_rejects_control_char_in_name() -> None:
    with pytest.raises(ValidationError):
        StockMetadata(
            code="600519",
            name="贵州\x00茅台",
            board=Board.SH_MAIN,
            price_limit_pct=0.10,
        )


def test_stock_metadata_pct_must_be_in_open_interval() -> None:
    with pytest.raises(ValidationError):
        StockMetadata(
            code="600519", name="x", board=Board.SH_MAIN, price_limit_pct=0.0
        )
    with pytest.raises(ValidationError):
        StockMetadata(
            code="600519", name="x", board=Board.SH_MAIN, price_limit_pct=1.0
        )


# ---------------------------------------------------------------------------
# build_metadata convenience constructor
# ---------------------------------------------------------------------------


def test_build_metadata_happy_path() -> None:
    md = build_metadata(code="300750", name="宁德时代")
    assert md.board is Board.CHUANGYE
    assert md.price_limit_pct == pytest.approx(0.20)
    assert md.lot_size == 100
    assert md.is_st is False


def test_build_metadata_marks_st() -> None:
    md = build_metadata(code="600519", name="*ST 茅台")
    assert md.is_st is True
    assert md.board is Board.SH_MAIN


def test_build_metadata_propagates_forbidden() -> None:
    with pytest.raises(ForbiddenCodeError) as exc:
        build_metadata(code="688981", name="中芯国际")
    assert exc.value.reason == "star_forbidden"


def test_build_metadata_propagates_unknown() -> None:
    with pytest.raises(UnknownCodeError):
        build_metadata(code="999999", name="x")


# ---------------------------------------------------------------------------
# Redline: no LLM/agent/mirofish imports anywhere in stock_metadata
# ---------------------------------------------------------------------------


def test_no_forbidden_imports_in_module() -> None:
    """``stock_metadata`` is the single source of truth for board+pct.

    It must never pull ``backend.{llm,agents,mirofish}`` — those carry
    network / OpenAI-style clients that would defeat the pure-module
    promise. ``backend.data`` is allowed (we live there).
    """
    src = inspect.getsource(sm)
    for forbidden in ("backend.llm", "backend.agents", "backend.mirofish"):
        assert forbidden not in src, f"forbidden import {forbidden} in stock_metadata"


def test_module_reload_is_idempotent() -> None:
    """No module-level mutable state should drift across reloads."""
    importlib.reload(sm)
    assert sm.classify_board("600519") is sm.Board.SH_MAIN
