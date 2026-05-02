"""CLI smoke tests for scripts/shadow_compare.py.

The math is exhaustively covered by tests/test_shadow_compare.py; here
we focus on argument plumbing, JSONL parsing, and exit codes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "shadow_compare.py"


def _load_script_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "_shadow_compare_cli", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(
    *,
    base_action: str = "买入",
    routed_action: str = "买入",
    base_conf: float = 0.7,
    routed_conf: float = 0.7,
    trade_date: str = "2026-05-02",
) -> dict:
    return {
        "run_id": "r1",
        "stock_code": "600519",
        "trade_date": trade_date,
        "baseline": {
            "action": base_action,
            "confidence": base_conf,
            "model": "kimi-k2.6",
            "latency_ms": 1000.0,
            "escalated": False,
            "parse_ok": True,
        },
        "routed": {
            "action": routed_action,
            "confidence": routed_conf,
            "model": "qwen3.6-plus",
            "latency_ms": 1500.0,
            "escalated": False,
            "parse_ok": True,
        },
    }


@pytest.mark.unit
class TestShadowCompareCLI:
    def test_jsonl_happy_path_returns_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            "\n".join(json.dumps(_doc()) for _ in range(20)) + "\n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path)])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "Total pairs: **20**" in captured
        assert "✅ action_match" in captured

    def test_strict_returns_one_on_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            json.dumps(_doc(routed_action="持有")) + "\n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path), "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "❌ action_match" in out

    def test_malformed_jsonl_raises(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        module = _load_script_module()
        with pytest.raises(SystemExit):
            module.main(["--input", str(path)])

    def test_missing_input_file_raises(self, tmp_path: Path) -> None:
        module = _load_script_module()
        missing = tmp_path / "does-not-exist.jsonl"
        with pytest.raises(SystemExit):
            module.main(["--input", str(missing)])

    def test_blank_jsonl_lines_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            f"\n{json.dumps(_doc())}\n   \n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Total pairs: **1**" in out

    @pytest.mark.parametrize("bad", ["0", "31", "-1", "abc"])
    def test_days_clamped(self, bad: str) -> None:
        # codex P5B-exit R5 MED: --days must be bounded so an unrealistic
        # argument cannot drive Mongo into an unbounded scan.
        module = _load_script_module()
        with pytest.raises(SystemExit):
            module._parse_args(["--days", bad])

    def test_jsonl_line_cap_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # codex P5B-exit R5 MED: a hostile dump must not OOM the runner.
        module = _load_script_module()
        monkeypatch.setattr(module, "_MAX_JSONL_LINES", 2)
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            "\n".join(json.dumps(_doc()) for _ in range(5)) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            module.main(["--input", str(path)])
