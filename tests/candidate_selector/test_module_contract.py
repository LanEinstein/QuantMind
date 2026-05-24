"""M-001 module contract: backend.candidate_selector isolation + public API.

The selector is a pure-quant Line-1 module (P0-8-amendment-2026-05-24 §2.3):
it reads quant features + advisory evidence but must never import
``backend.{llm,agents,mirofish}`` — the bright line that keeps MiroFish an
advisor, not a decider. redline-check ``[L-002]`` enforces the same closure.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.candidate_selector as candidate_selector

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "mirofish"}
_ROOT = pathlib.Path("backend/candidate_selector")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Return every ``backend.{llm,agents,mirofish}`` import under ``root``.

    Covers absolute (``import backend.llm`` / ``from backend.agents import x`` /
    ``from backend import llm``) and package-relative (``from ..llm import x``)
    forms.
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
    def test_scanner_catches_planted_violation(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend.llm.router import LLMRouter\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "src",
        [
            "import backend.agents\n",
            "from backend.mirofish import x\n",
            "from backend import llm\n",
            "from ..agents import x\n",
            "from .. import mirofish\n",
        ],
    )
    def test_scanner_catches_violation_forms(
        self, tmp_path: pathlib.Path, src: str
    ) -> None:
        (tmp_path / "bad.py").write_text(src, encoding="utf-8")
        assert _forbidden_backend_imports(tmp_path), src


class TestPublicAPI:
    @pytest.mark.unit
    def test_exports_core_symbols(self) -> None:
        for name in (
            "CandidateSelector",
            "CandidateSelection",
            "QuantCandidate",
            "AdvisorySignal",
            "SelectorConfig",
            "CandidateSelectorError",
            "load_selector_config",
        ):
            assert hasattr(candidate_selector, name), f"missing {name}"

    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in candidate_selector.__all__:
            assert hasattr(candidate_selector, name), f"__all__ missing {name}"
