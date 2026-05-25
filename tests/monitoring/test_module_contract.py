"""N-005 module contract: backend.monitoring import isolation.

Line-2 monitoring is deterministic / pure-quant: the SELL/ADD direction is a
quant observation, never an LLM output. The module therefore must NOT import
``backend.{llm,agents,agents_team,mirofish}`` (``backend/monitoring/CLAUDE.md``
red line + P0-10-amendment-2026-05-25 §2.5). ``agents_team`` is forbidden too:
it is the Line-1 LLM debate orchestration (``run_shortlist`` / ``fund_manager``),
so importing it would smuggle the multi-agent LLM path back into the zero-LLM
Line-2 decision path (codex N-005). ``backend.{broker,data,risk,services,
integrations,marketdata_snapshot,models}`` ARE allowed dependencies (positions
/ data-quality / RiskEngine / the single-construction-point builder / renderer).

This AST scan is the by-construction guard paired with redline-check.sh
``[N-005]``; the triggered LLM (N-004) is reached only through ``cost_guard``
(reservation) + Redis, with the actual call orchestrated OUTSIDE this module.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "agents_team", "mirofish"}
_ROOT = pathlib.Path("backend/monitoring")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Return every ``backend.{llm,agents,mirofish}`` import under ``root``.

    Covers absolute (``import backend.llm`` / ``from backend.agents import x``)
    and package-relative (``from ..llm import x``) forms.
    """
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        len(parts) >= 2
                        and parts[0] == "backend"
                        and parts[1] in FORBIDDEN_SUBPACKAGES
                    ):
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                parts = mod.split(".") if mod else []
                names = [a.name for a in node.names]
                if (
                    node.level == 0
                    and len(parts) >= 2
                    and parts[0] == "backend"
                    and parts[1] in FORBIDDEN_SUBPACKAGES
                ):
                    violations.append(f"{path}: from {mod} import ...")
                if (
                    node.level == 0
                    and mod == "backend"
                    and any(n in FORBIDDEN_SUBPACKAGES for n in names)
                ):
                    violations.append(f"{path}: from backend import <forbidden>")
                if node.level > 0 and parts and parts[0] in FORBIDDEN_SUBPACKAGES:
                    dots = "." * node.level
                    violations.append(f"{path}: from {dots}{mod} import ...")
                if node.level > 0 and any(n in FORBIDDEN_SUBPACKAGES for n in names):
                    violations.append(f"{path}: relative import of <forbidden>")
    return violations


class TestImportIsolation:
    @pytest.mark.unit
    def test_no_forbidden_subpackage_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    @pytest.mark.unit
    def test_scanner_catches_planted_absolute(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend.llm.router import LLMRouter\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_planted_relative(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "bad.py").write_text(
            "from ..agents import collector\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_import_form(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "bad.py").write_text(
            "import backend.mirofish.extractors\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_agents_team(self, tmp_path: pathlib.Path) -> None:
        # agents_team is the Line-1 LLM debate path — importing it into Line-2
        # would reintroduce the multi-agent LLM debate (codex N-005).
        (tmp_path / "bad.py").write_text(
            "from backend.agents_team.graph import run_shortlist\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_from_backend_import_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend import agents_team\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)


class TestPublicApi:
    @pytest.mark.unit
    def test_modules_import_clean(self) -> None:
        # The whole Line-2 surface imports without pulling llm/agents/mirofish.
        import backend.monitoring.add_position as add_position
        import backend.monitoring.anomaly as anomaly
        import backend.monitoring.degrade as degrade
        import backend.monitoring.sell_signal as sell_signal

        assert hasattr(anomaly, "AnomalyDetector")
        assert hasattr(sell_signal, "evaluate_sell_intents")
        assert hasattr(add_position, "evaluate_add_intents")
        assert hasattr(degrade, "partition_by_suspension")
