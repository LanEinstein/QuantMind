"""L-005 module contract: backend.screening import isolation + public API."""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.screening as screening

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "mirofish"}
_ROOT = pathlib.Path("backend/screening")


def _forbidden_backend_imports(root: pathlib.Path) -> list[str]:
    """Return every ``backend.{llm,agents,mirofish}`` import under ``root``.

    Covers both absolute (``import backend.llm`` / ``from backend.agents
    import x``) and package-relative (``from ..llm import x``) forms.
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
                # absolute: from backend.<sub> import x
                if (
                    node.level == 0
                    and len(parts) >= 2
                    and parts[0] == "backend"
                    and parts[1] in FORBIDDEN_SUBPACKAGES
                ):
                    violations.append(f"{path}: from {mod} import ...")
                # absolute: from backend import <sub> (sub in names)
                if (
                    node.level == 0
                    and mod == "backend"
                    and any(n in FORBIDDEN_SUBPACKAGES for n in names)
                ):
                    violations.append(f"{path}: from backend import <forbidden>")
                # relative: from ..<sub> import x (module first part forbidden)
                if node.level > 0 and parts and parts[0] in FORBIDDEN_SUBPACKAGES:
                    dots = "." * node.level
                    violations.append(f"{path}: from {dots}{mod} import ...")
                # relative: from .. import <sub> (sub in names)
                if node.level > 0 and any(n in FORBIDDEN_SUBPACKAGES for n in names):
                    violations.append(f"{path}: relative import of <forbidden>")
    return violations


class TestImportIsolation:
    @pytest.mark.unit
    def test_no_forbidden_subpackage_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    @pytest.mark.unit
    def test_scanner_catches_planted_violation(self, tmp_path: pathlib.Path) -> None:
        # Self-test: the scanner must flag a synthetic forbidden import so a
        # future refactor cannot silently weaken it.
        (tmp_path / "bad.py").write_text(
            "from backend.llm.router import LLMRouter\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    def test_scanner_catches_relative_violation(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "bad.py").write_text(
            "from ..agents import x\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "src",
        [
            "from backend import llm\n",          # forbidden in names, not module
            "from backend import agents, data\n",  # mixed
            "from .. import mirofish\n",            # relative, forbidden in names
        ],
    )
    def test_scanner_catches_name_level_violations(
        self, tmp_path: pathlib.Path, src: str
    ) -> None:
        (tmp_path / "bad.py").write_text(src, encoding="utf-8")
        assert _forbidden_backend_imports(tmp_path), src


class TestPublicAPI:
    @pytest.mark.unit
    def test_exports_core_symbols(self) -> None:
        for name in (
            "Screener",
            "ScreenResult",
            "CandidateRow",
            "ExcludedRow",
            "ExclusionReason",
            "ScreeningError",
            "FactorVector",
            "compute_factors",
            "FEATURE_CODE_VERSION",
        ):
            assert hasattr(screening, name), f"backend.screening missing {name}"

    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in screening.__all__:
            assert hasattr(screening, name), f"__all__ lists missing {name}"
