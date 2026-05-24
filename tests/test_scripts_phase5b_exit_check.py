"""CLI smoke test for scripts/phase5b_exit_check.py.

The aggregation math is exhaustively covered by
tests/test_phase5b_exit_check.py; this file pins the argparse surface
+ the live-IO wiring (Mongo projection + window filter, Redis URL) so
the perf fixes flagged in codex P5B-exit R3 cannot regress.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "phase5b_exit_check.py"
)


def _load_script_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "_phase5b_exit_cli", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestExitCheckCLI:
    def test_missing_policy_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load_script_module()
        missing = tmp_path / "universe_policy.yaml"
        rc = module.main(["--policy-path", str(missing)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "universe_policy.yaml not found" in err

    def test_arg_parser_defaults(self) -> None:
        module = _load_script_module()
        ns = module._parse_args(["--days", "3"])
        assert ns.days == 3
        assert ns.strict is False
        assert ns.policy_path == Path("config/universe_policy.yaml")

    @pytest.mark.parametrize("bad", ["0", "31", "-1", "1000", "abc"])
    def test_days_clamped(self, bad: str) -> None:
        module = _load_script_module()
        with pytest.raises(SystemExit):
            module._parse_args(["--days", bad])


class _AsyncIter:
    def __init__(self, items: list) -> None:  # noqa: ANN001
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self):  # type: ignore[no-untyped-def]
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


@pytest.mark.unit
class TestExitCheckCLILiveInputs:
    """Mock motor + Redis to exercise the perf-sensitive query shape.

    The live path was previously untested (codex P5B-exit R4 MED) so
    the projection + window filter could regress without a failing
    test. We pin both here — and the AsyncIOMotorClient.close + Redis
    aclose calls — so the resource lifecycle stays tight.
    """

    def test_gather_inputs_uses_projection_and_window(
        self, tmp_path: Path
    ) -> None:
        module = _load_script_module()

        # Build a Mongo cursor that records its find call args and
        # yields one minimal record document.
        find_mock = MagicMock()
        cursor = _AsyncIter(
            [
                {
                    "run_id": "r1",
                    "stock_code": "600519",
                    "trade_date": "2026-05-02",
                    "created_at": _dt.datetime(
                        2026, 5, 2, 9, 0, tzinfo=_dt.UTC
                    ),
                    "completed_at": _dt.datetime(
                        2026, 5, 2, 9, 5, tzinfo=_dt.UTC
                    ),
                }
            ]
        )
        cursor_with_sort = MagicMock()
        cursor_with_sort.__aiter__ = lambda self: cursor.__aiter__()
        find_mock.return_value = MagicMock(
            sort=MagicMock(return_value=cursor_with_sort)
        )
        coll = MagicMock(find=find_mock)
        db = MagicMock()
        db.__getitem__.return_value = coll

        mongo_client = MagicMock()
        mongo_client.__getitem__ = MagicMock(return_value=db)
        mongo_client.close = MagicMock()

        redis_client = MagicMock()
        redis_client.aclose = AsyncMock()

        # aggregate_costs and query_shadow_decisions are imported inside
        # the function — patch their canonical module paths.
        from collections import namedtuple

        FakeSummary = namedtuple("FakeSummary", "entries")
        FakeEntry = namedtuple(
            "FakeEntry", "date agent_name provider cost_rmb"
        )
        fake_summary = FakeSummary(
            entries=(
                FakeEntry(
                    date="2026-05-02",
                    agent_name="fund_manager",
                    provider="kimi",
                    cost_rmb=0.1,
                ),
            )
        )

        ns = module._parse_args(["--days", "3"])

        with (
            patch(
                "motor.motor_asyncio.AsyncIOMotorClient",
                return_value=mongo_client,
            ),
            patch("redis.asyncio.Redis") as redis_cls,
            patch(
                "backend.llm.cost_tracker.aggregate_costs",
                AsyncMock(return_value=fake_summary),
            ),
            patch(
                "backend.services.shadow_recorder.query_shadow_decisions",
                AsyncMock(return_value=[]),
            ),
        ):
            redis_cls.from_url = MagicMock(return_value=redis_client)
            import asyncio

            records, costs, shadows = asyncio.run(
                module._gather_inputs(ns)
            )

        # Window filter present and projection limited to scalar fields.
        find_args, _ = find_mock.call_args
        query_filter, projection = find_args
        assert "$or" in query_filter
        assert projection == {
            "_id": 0,
            "run_id": 1,
            "stock_code": 1,
            "trade_date": 1,
            "created_at": 1,
            "completed_at": 1,
        }
        # Resources closed.
        mongo_client.close.assert_called_once()
        redis_client.aclose.assert_awaited_once()
        # Records flowed through.
        assert len(records) == 1
        assert costs[0]["cost_rmb"] == 0.1
        assert shadows == []
