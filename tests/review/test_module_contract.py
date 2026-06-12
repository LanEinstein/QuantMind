"""AA-005 module contract: backend.review import isolation.

The attribution review module is the objective-evidence substrate for
the Phase AB promotion engine, so it must stay pure-deterministic: no
``backend.{llm,agents,agents_team,mirofish}`` import (LLM review prose,
if any ever exists, lives in the orchestration layer and goes to
``evidence_collection`` only) and no ``InstructionPlan(`` construction
(the single-construction-point red line, R0 §4).

This AST scan pairs with redline-check.sh ``[AA-005]`` (the grep is the
standalone-CI fast gate; this test is the authoritative guard).
"""

from __future__ import annotations

import ast
import pathlib

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "agents_team", "mirofish"}
_ROOT = pathlib.Path("backend/review")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Every forbidden-subpackage import under ``root`` (all forms)."""
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
                    and parts == ["backend"]
                    and FORBIDDEN_SUBPACKAGES.intersection(names)
                ):
                    violations.append(
                        f"{path}: from backend import "
                        f"{sorted(FORBIDDEN_SUBPACKAGES & set(names))}"
                    )
                if node.level > 0 and (
                    (parts and parts[0] in FORBIDDEN_SUBPACKAGES)
                    or (not parts and FORBIDDEN_SUBPACKAGES & set(names))
                ):
                    violations.append(
                        f"{path}: relative import of {mod or names}"
                    )
    return violations


class TestReviewModuleContract:
    def test_no_llm_or_agent_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    def test_no_instruction_plan_construction(self) -> None:
        """Single construction point (R0 §4): review never builds plans."""
        offenders: list[str] = []
        for path in sorted(_ROOT.rglob("*.py")):
            if "InstructionPlan(" in path.read_text(encoding="utf-8"):
                offenders.append(str(path))
        assert offenders == []

    def test_module_files_exist(self) -> None:
        """Guard against the scan silently passing on an empty dir."""
        files = sorted(p.name for p in _ROOT.glob("*.py"))
        assert {
            "attribution.py",
            "models.py",
            "ops_gate.py",
            "store.py",
            "weekly.py",
        }.issubset(set(files))
