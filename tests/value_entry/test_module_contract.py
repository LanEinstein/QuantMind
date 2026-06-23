"""AF-004 module contract: backend.value_entry import isolation + EntryGate."""

from __future__ import annotations

import ast
import pathlib

import pytest

import backend.value_entry as pkg
from backend.value_assembly.assembler import EntryGate
from backend.value_entry.bottom_confirmation import BottomConfirmation

FORBIDDEN_SUBPACKAGES = {"llm", "agents", "mirofish"}
_ROOT = pathlib.Path("backend/value_entry")


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
                    violations.append(f"{path}: relative forbidden import")
                if node.level > 0 and any(n in FORBIDDEN_SUBPACKAGES for n in names):
                    violations.append(f"{path}: relative import of <forbidden>")
    return violations


class TestImportIsolation:
    @pytest.mark.unit
    def test_no_forbidden_subpackage_imports(self) -> None:
        assert _forbidden_backend_imports(_ROOT) == []

    @pytest.mark.unit
    def test_scanner_catches_planted_violation(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "bad.py").write_text(
            "from backend.llm import x\n", encoding="utf-8"
        )
        assert _forbidden_backend_imports(tmp_path)


class TestEntryGateProtocol:
    @pytest.mark.unit
    def test_bottom_confirmation_satisfies_entry_gate(self) -> None:
        gate = BottomConfirmation(windows={}, cyq_by_code={})
        # Structural Protocol conformance: BottomConfirmation is an EntryGate.
        assert isinstance(gate, EntryGate)


class TestPublicAPI:
    @pytest.mark.unit
    def test_all_importable(self) -> None:
        for name in pkg.__all__:
            assert hasattr(pkg, name), f"__all__ lists missing {name}"
