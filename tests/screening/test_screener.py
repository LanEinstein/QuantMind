"""Tests for backend.screening.screener (full-market quant pre-filter)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.marketdata_snapshot import MarketDataSnapshot
from backend.screening.screener import (
    MIN_HISTORY_BARS,
    CandidateRow,
    ExclusionReason,
    Screener,
    ScreeningError,
    ScreenResult,
)
from backend.services.universe_policy import ExclusionRules

_HEADER = "ts_code,name,listed_trading_days,closes,amounts"


def _snapshot(csv_text: str) -> MarketDataSnapshot:
    raw = csv_text.encode("utf-8")
    return MarketDataSnapshot(
        vendor="tushare",
        endpoint="screen_frame",
        params={"trade_date": "20260522"},
        trade_date="20260522",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
    )


def _series(values: list[float]) -> str:
    return "|".join(repr(v) for v in values)


def _row(
    *,
    ts_code: str = "600519.SH",
    name: str = "贵州茅台",
    listed: int | str = 300,
    closes: list[float] | None = None,
    amounts: list[float] | None = None,
) -> str:
    if closes is None:
        closes = [10.0 + i * 0.1 for i in range(25)]  # 25 bars, rising
    if amounts is None:
        amounts = [3e8] * 25  # avg 3e8 ≥ 2e8 liquidity floor
    return f"{ts_code},{name},{listed},{_series(closes)},{_series(amounts)}"


def _frame(rows: list[str]) -> str:
    return "\n".join([_HEADER, *rows])


def _screener(**kw) -> Screener:
    return Screener(ExclusionRules(), **kw)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.unit
    def test_single_valid_candidate(self) -> None:
        snap = _snapshot(_frame([_row()]))
        result = _screener().screen(snap, "SIG-1")
        assert isinstance(result, ScreenResult)
        assert len(result.candidates) == 1
        cand = result.candidates[0]
        assert isinstance(cand, CandidateRow)
        assert cand.code == "600519"
        assert cand.board.value == "sh_main"
        assert cand.last_price == pytest.approx(10.0 + 24 * 0.1)
        assert cand.factors.avg_amount_20d == pytest.approx(3e8)
        assert result.universe_size == 1

    @pytest.mark.unit
    def test_etf_code_is_whitelisted(self) -> None:
        snap = _snapshot(_frame([_row(ts_code="510300.SH", name="沪深300ETF")]))
        result = _screener().screen(snap, "SIG-ETF")
        assert [c.code for c in result.candidates] == ["510300"]
        assert result.candidates[0].board.value == "etf"


# ---------------------------------------------------------------------------
# Fail-closed exclusions
# ---------------------------------------------------------------------------


class TestExclusionsFailClosed:
    def _reason(self, row: str) -> ExclusionReason:
        result = _screener().screen(_snapshot(_frame([row])), "SIG")
        assert not result.candidates
        assert len(result.excluded) == 1
        return result.excluded[0].reason

    @pytest.mark.unit
    def test_forbidden_board_star(self) -> None:
        reason = self._reason(_row(ts_code="688001.SH"))
        assert reason == ExclusionReason.FORBIDDEN_BOARD

    @pytest.mark.unit
    def test_forbidden_board_beijiao(self) -> None:
        reason = self._reason(_row(ts_code="830001.BJ"))
        assert reason == ExclusionReason.FORBIDDEN_BOARD

    @pytest.mark.unit
    def test_forbidden_convertible_bond(self) -> None:
        reason = self._reason(_row(ts_code="113001.SH"))
        assert reason == ExclusionReason.FORBIDDEN_BOARD

    @pytest.mark.unit
    def test_is_st_excluded(self) -> None:
        assert self._reason(_row(name="*ST 康得")) == ExclusionReason.IS_ST

    @pytest.mark.unit
    def test_ipo_too_new(self) -> None:
        assert self._reason(_row(listed=10)) == ExclusionReason.IPO_TOO_NEW

    @pytest.mark.unit
    def test_missing_listed_days_fails_closed_as_ipo(self) -> None:
        assert self._reason(_row(listed="")) == ExclusionReason.IPO_TOO_NEW

    @pytest.mark.unit
    def test_sub_new_too_new(self) -> None:
        assert self._reason(_row(listed=100)) == ExclusionReason.SUB_NEW_TOO_NEW

    @pytest.mark.unit
    def test_insufficient_history(self) -> None:
        short = [10.0 + i for i in range(MIN_HISTORY_BARS - 1)]
        amts = [3e8] * (MIN_HISTORY_BARS - 1)
        assert (
            self._reason(_row(closes=short, amounts=amts))
            == ExclusionReason.INSUFFICIENT_HISTORY
        )

    @pytest.mark.unit
    def test_liquidity_too_low(self) -> None:
        assert (
            self._reason(_row(amounts=[1e7] * 25)) == ExclusionReason.LIQUIDITY_TOO_LOW
        )

    @pytest.mark.unit
    def test_price_too_high(self) -> None:
        high = [600.0 + i for i in range(25)]  # last > 500 cap
        assert self._reason(_row(closes=high)) == ExclusionReason.PRICE_TOO_HIGH

    @pytest.mark.unit
    def test_board_not_whitelisted_when_narrowed(self) -> None:
        # sh_main code with a whitelist that excludes sh_main.
        scr = Screener(ExclusionRules(), board_whitelist=frozenset({"etf"}))
        result = scr.screen(_snapshot(_frame([_row()])), "SIG")
        assert not result.candidates
        assert result.excluded[0].reason == ExclusionReason.BOARD_NOT_WHITELISTED

    @pytest.mark.unit
    def test_malformed_row_dropped_not_candidate(self) -> None:
        frame = _frame([_row(), "garbage,row,with,too,many,cols,here"])
        result = _screener().screen(_snapshot(frame), "SIG")
        assert [c.code for c in result.candidates] == ["600519"]
        assert result.excluded_counts.get("malformed_row") == 1

    @pytest.mark.unit
    def test_unparseable_closes_dropped(self) -> None:
        bad = "600000.SH,浦发银行,300,1.0|x|3.0,3e8|3e8|3e8"
        result = _screener().screen(_snapshot(_frame([bad])), "SIG")
        assert not result.candidates
        assert result.excluded_counts.get("malformed_row") == 1

    @pytest.mark.unit
    def test_duplicate_code_all_copies_dropped(self) -> None:
        # Both copies of a duplicated code are dropped fail-closed (not just
        # the later one) so no arbitrary version is ranked and replay holds.
        frame = _frame([_row(), _row(), _row(ts_code="600002.SH", name="ok")])
        result = _screener().screen(_snapshot(frame), "SIG")
        assert [c.code for c in result.candidates] == ["600002"]
        assert result.excluded_counts.get("malformed_row") == 2

    @pytest.mark.unit
    def test_nan_token_fails_closed_as_malformed(self) -> None:
        nans = "|".join(["nan"] * 25)
        bad = f"600000.SH,浦发银行,300,{nans},{_series([3e8] * 25)}"
        result = _screener().screen(_snapshot(_frame([bad])), "SIG")
        assert not result.candidates
        assert result.excluded_counts.get("malformed_row") == 1

    @pytest.mark.unit
    def test_inf_token_fails_closed_as_malformed(self) -> None:
        infs = "|".join(["inf"] * 25)
        closes = _series([10.0 + i for i in range(25)])
        bad = f"600000.SH,浦发银行,300,{closes},{infs}"
        result = _screener().screen(_snapshot(_frame([bad])), "SIG")
        assert not result.candidates
        assert result.excluded_counts.get("malformed_row") == 1

    @pytest.mark.unit
    def test_undefined_factor_fails_closed_not_crash(self) -> None:
        # A zero at the -21 close makes momentum_20d undefined despite >=21
        # bars: the row must fail closed (unscorable_factor), not abort the
        # whole screen with a TypeError.
        closes = [10.0 + i * 0.1 for i in range(25)]
        closes[4] = 0.0  # closes[-21]
        result = _screener().screen(_snapshot(_frame([_row(closes=closes)])), "SIG")
        assert not result.candidates
        assert result.excluded[0].reason == ExclusionReason.UNSCORABLE_FACTOR


# ---------------------------------------------------------------------------
# Ranking, top-N, determinism
# ---------------------------------------------------------------------------


class TestRankingAndTopN:
    def _rising(self, slope: float) -> list[float]:
        return [10.0 + i * slope for i in range(25)]

    @pytest.mark.unit
    def test_top_n_truncation(self) -> None:
        rows = [
            _row(ts_code=f"60000{i}.SH", name=f"n{i}", closes=self._rising(0.1 + i))
            for i in range(5)
        ]
        scr = _screener(top_n_cap=3)
        result = scr.screen(_snapshot(_frame(rows)), "SIG")
        assert len(result.candidates) == 3  # capped
        assert result.universe_size == 5

    @pytest.mark.unit
    def test_higher_momentum_ranks_higher(self) -> None:
        rows = [
            _row(ts_code="600001.SH", name="lo", closes=self._rising(0.05)),
            _row(ts_code="600002.SH", name="hi", closes=self._rising(2.0)),
        ]
        result = _screener().screen(_snapshot(_frame(rows)), "SIG")
        assert [c.code for c in result.candidates] == ["600002", "600001"]
        assert result.candidates[0].score >= result.candidates[1].score

    @pytest.mark.unit
    def test_deterministic_repeat(self) -> None:
        rows = [
            _row(
                ts_code=f"60000{i}.SH", name=f"n{i}", closes=self._rising(0.1 + i * 0.3)
            )
            for i in range(4)
        ]
        snap = _snapshot(_frame(rows))
        r1 = _screener().screen(snap, "SIG")
        r2 = _screener().screen(snap, "SIG")
        assert [(c.code, c.score) for c in r1.candidates] == [
            (c.code, c.score) for c in r2.candidates
        ]

    @pytest.mark.unit
    def test_tie_break_by_code_ascending(self) -> None:
        # Two identical series → identical scores → code asc tie-break.
        rows = [
            _row(ts_code="600009.SH", name="b"),
            _row(ts_code="600003.SH", name="a"),
        ]
        result = _screener().screen(_snapshot(_frame(rows)), "SIG")
        assert [c.code for c in result.candidates] == ["600003", "600009"]


# ---------------------------------------------------------------------------
# Manifest lineage + structural errors
# ---------------------------------------------------------------------------


class TestManifestAndErrors:
    @pytest.mark.unit
    def test_manifest_records_every_parsed_row(self) -> None:
        # 1 candidate + 1 excluded (both are consumed inputs)
        rows = [_row(ts_code="600519.SH"), _row(ts_code="688001.SH")]
        snap = _snapshot(_frame(rows))
        result = _screener().screen(snap, "SIG-LINEAGE")
        m = result.manifest
        assert m.signal_id == "SIG-LINEAGE"
        assert m.snapshot_ids == (snap.snapshot_id,)
        # both parsed rows are consumed inputs (exclusion decisions count)
        assert len(m.consumed_rows) == 2
        assert m.feature_code_version == "screening.factors/v1"
        assert "screening_config" in m.config_hashes

    @pytest.mark.unit
    def test_config_hash_reflects_effective_config(self) -> None:
        # The manifest config hash must change when the effective screening
        # config changes, so a replay can detect a different shortlist would
        # be produced (PIT lineage integrity).
        snap = _snapshot(_frame([_row()]))
        h_default = _screener().screen(snap, "S").manifest.config_hashes[
            "screening_config"
        ]
        h_same = _screener().screen(snap, "S").manifest.config_hashes[
            "screening_config"
        ]
        h_cap = _screener(top_n_cap=7).screen(snap, "S").manifest.config_hashes[
            "screening_config"
        ]
        assert h_default == h_same  # deterministic
        assert h_default != h_cap  # sensitive to config

    @pytest.mark.unit
    def test_non_csv_encoding_rejected(self) -> None:
        raw = b"\x00\x01"
        snap = MarketDataSnapshot(
            vendor="tushare",
            endpoint="x",
            trade_date="20260522",
            raw_payload=raw,
            size=len(raw),
            encoding="parquet",
            compression="none",
            raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
            fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
        )
        with pytest.raises(ScreeningError, match="csv"):
            _screener().screen(snap, "SIG")

    @pytest.mark.unit
    def test_bad_header_rejected(self) -> None:
        snap = _snapshot("wrong,header\n600519.SH,x")
        with pytest.raises(ScreeningError, match="header"):
            _screener().screen(snap, "SIG")

    @pytest.mark.unit
    def test_empty_universe_yields_no_candidates(self) -> None:
        result = _screener().screen(_snapshot(_HEADER), "SIG")
        assert result.candidates == ()
        assert result.universe_size == 0
        assert result.manifest.consumed_rows == ()


class TestImportIsolation:
    """0-LLM by construction (full AST contract lives in L-005)."""

    @pytest.mark.unit
    def test_screening_source_has_no_llm_imports(self) -> None:
        import pathlib

        root = pathlib.Path("backend/screening")
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("backend.llm", "backend.agents", "backend.mirofish"):
                assert f"import {forbidden}" not in text
                assert f"from {forbidden}" not in text
