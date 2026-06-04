"""Q-004 module contract: backend.knowledge_graph import isolation + API.

The KG is a pure local artifact, NOT a runtime decision path — it must
never reach back into the trading stack (P2-2-amendment-2026-05-24;
module CLAUDE.md red line: forbid backend.{api,broker,risk,llm,agents,
mirofish,data}, preventing reverse calls that bypass the gates).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.knowledge_graph as kg

FORBIDDEN_SUBPACKAGES = {
    "api", "broker", "risk", "llm", "agents", "mirofish", "data",
}
_ROOT = pathlib.Path("backend/knowledge_graph")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Every ``backend.{forbidden}`` import under ``root`` — absolute
    (``import backend.llm`` / ``from backend.agents import x`` /
    ``from backend import llm``) and package-relative (``from ..llm
    import x`` / ``from .. import llm``) forms (mirrors L-005 scanner).
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
                    violations.append(f"{path}: from {'.' * node.level}{mod} ...")
                if node.level > 0 and any(
                    n in FORBIDDEN_SUBPACKAGES for n in names
                ):
                    violations.append(f"{path}: relative import of <forbidden>")
    return violations


class TestImportIsolation:
    @pytest.mark.unit
    def test_no_forbidden_subpackage_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    @pytest.mark.unit
    def test_scanner_catches_planted_absolute(self, tmp_path: pathlib.Path) -> None:
        # Self-test so a refactor cannot silently weaken the scanner.
        (tmp_path / "bad.py").write_text(
            "from backend.broker.mock_broker import MockBroker\n",
            encoding="utf-8",
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_planted_relative(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "bad.py").write_text(
            "from ..risk import engine\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_from_backend_import(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend import data\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)


class TestPublicApi:
    @pytest.mark.unit
    def test_package_exports(self) -> None:
        for name in (
            "KGNode", "KGEdge", "NodeType", "EdgeType", "NodeStatus",
            "EDGE_ENDPOINTS", "SqliteKGStore", "KnowledgeGraphStore",
        ):
            assert hasattr(kg, name), name

    @pytest.mark.unit
    def test_no_write_http_surface(self) -> None:
        # The KG must add no API router at all (only 2 write endpoints
        # exist backend-wide; KG ingest approval is an offline human act).
        for path in sorted(_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            assert "APIRouter" not in text, path
            assert "fastapi" not in text.lower(), path
