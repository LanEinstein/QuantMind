"""V-002 module contract: backend.slot_portfolio isolation + no InstructionPlan.

The two structural red lines for the rotation layer:

1. **Import isolation** — never ``import backend.{llm,agents,mirofish}`` (pure
   quant; the decision uses only Line-1 quant + deterministic Line-2 health).
2. **Never constructs an InstructionPlan** — R0 §4 single construction point.
   This layer only proposes / records intent; side/volume/limit_price stay
   deterministically derived by the builder.

Both are also enforced by ``scripts/redline-check.sh [V-002]``; this AST test is
the authoritative in-suite guard (with self-tests that flag planted violations).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.slot_portfolio as slot_portfolio

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "mirofish"}
_ROOT = pathlib.Path("backend/slot_portfolio")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Return every ``backend.{llm,agents,mirofish}`` import under ``root``.

    Covers absolute (``import backend.llm`` / ``from backend.agents import x`` /
    ``from backend import llm``) and package-relative (``from ..llm import x`` /
    ``from .. import llm``) forms.
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


def _constructs_instruction_plan(root: pathlib.Path) -> list[str]:
    """Return any site under ``root`` that names the InstructionPlan ctor.

    Mirrors the redline-check [M-004] AST scan: an aliased import
    (``from ... import InstructionPlan as Plan; Plan(...)``) or an attribute
    call (``module.InstructionPlan(...)``) both count.
    """
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
            "from ..mirofish import x\n",
            "from backend import agents\n",
            "from .. import llm\n",
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
    def test_exports_core_symbols(self) -> None:
        for name in (
            "IncumbentState",
            "ChallengerState",
            "IncumbentWeakness",
            "ChallengerMargin",
            "RotationPolicyConfig",
            "RotationProposal",
            "SlotPortfolioError",
            "evaluate_incumbent_weakness",
            "evaluate_challenger_margin",
            "propose_rotation",
            "load_rotation_policy_config",
        ):
            assert hasattr(slot_portfolio, name), f"missing {name}"

    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in slot_portfolio.__all__:
            assert hasattr(slot_portfolio, name), f"__all__ lists missing {name}"
