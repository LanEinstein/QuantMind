"""W-001 module contract: backend.position_thesis isolation + no InstructionPlan.

Two structural red lines (P0-10-amendment-line2-2026-06-01 §3 / R0 §4):

1. **Import isolation** — never ``import backend.{llm,agents,agents_team,mirofish}``
   (the deterministic threshold derivation is pure quant — the LLM only writes
   the opaque pillar text upstream).
2. **Never constructs an InstructionPlan** — the thesis is advisory data, never
   an order; a SELL it may justify is built downstream by the builder.

Both are also enforced by ``scripts/redline-check.sh [W-001]``; this AST test is
the authoritative in-suite guard (with self-tests that flag planted violations).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.position_thesis as position_thesis

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "agents_team", "mirofish"}
_ROOT = pathlib.Path("backend/position_thesis")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
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


def _constructs_instruction_plan(root: pathlib.Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "InstructionPlan":
                        names.add(a.asname or a.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id in names:
                violations.append(f"{path}:{node.lineno}: InstructionPlan(...)")
            elif isinstance(f, ast.Attribute) and f.attr == "InstructionPlan":
                violations.append(f"{path}:{node.lineno}: *.InstructionPlan(...)")
    return violations


class TestImportIsolation:
    @pytest.mark.unit
    def test_no_forbidden_subpackage_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "src",
        [
            "from backend.llm.router import LLMRouter\n",
            "import backend.agents.graph\n",
            "from backend.agents_team.graph import run_shortlist\n",
            "from ..mirofish import x\n",
            "from backend import agents\n",
        ],
    )
    def test_scanner_catches_planted_violation(
        self, tmp_path: pathlib.Path, src: str
    ) -> None:
        (tmp_path / "bad.py").write_text(src, encoding="utf-8")
        assert _forbidden_backend_imports(tmp_path), src


class TestNoInstructionPlanConstruction:
    @pytest.mark.unit
    def test_module_never_constructs_instruction_plan(self) -> None:
        assert _constructs_instruction_plan(_ROOT) == []

    @pytest.mark.unit
    def test_scanner_catches_planted_construction(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend.models.instruction import InstructionPlan as Plan\n"
            "x = Plan(volume=5000)\n",
            encoding="utf-8",
        )
        assert _constructs_instruction_plan(tmp_path)


class TestPublicAPI:
    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in position_thesis.__all__:
            assert hasattr(position_thesis, name), f"__all__ missing {name}"
