"""P-002 module contract: backend.portfolio_allocation import isolation + public API.

Cloned from tests/budget_policy/test_module_contract.py (P0-7-amendment-2026-05-30
redline ``[P-002]``): the allocation layer is a pure upstream module — it must
never import ``backend.{llm,agents,mirofish}`` (and is not imported by
``backend/risk/``; that direction is covered by the redline-check grep)."""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.portfolio_allocation as portfolio_allocation

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "mirofish"}
_ROOT = pathlib.Path("backend/portfolio_allocation")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Return every ``backend.{llm,agents,mirofish}`` import under ``root``
    (absolute + package-relative forms)."""
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
    @pytest.mark.parametrize(
        "src",
        [
            "import backend.mirofish.simulator\n",
            "from backend.agents import x\n",
            "from backend import llm\n",        # forbidden in names, not module
            "from .. import agents\n",           # relative, forbidden in names
            "from ..mirofish import y\n",
        ],
    )
    def test_scanner_catches_planted_violation(
        self, tmp_path: pathlib.Path, src: str
    ) -> None:
        (tmp_path / "bad.py").write_text(src, encoding="utf-8")
        assert _forbidden_backend_imports(tmp_path), src


class TestPublicAPI:
    @pytest.mark.unit
    def test_exports_core_symbols(self) -> None:
        for name in (
            "AllocationPolicy",
            "AllocationPolicyError",
            "INVERSE_VOLATILITY",
            "load_allocation_policy",
            "inverse_vol_weights",
            "compute_target_cash",
            "cash_to_lots",
            "deployable_cash",
        ):
            assert hasattr(portfolio_allocation, name), (
                f"portfolio_allocation missing {name}"
            )

    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in portfolio_allocation.__all__:
            assert hasattr(portfolio_allocation, name), f"__all__ lists missing {name}"
