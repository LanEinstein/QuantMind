"""AF-003 — quality_fundamentals must stay pure (no LLM/agents/mirofish)."""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path("backend/quality_fundamentals")
_FORBIDDEN = ("backend.llm", "backend.agents", "backend.mirofish")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_no_forbidden_imports() -> None:
    files = sorted(_PKG.glob("*.py"))
    assert files, "quality_fundamentals package has no modules to scan"
    for path in files:
        for mod in _imported_modules(path):
            assert not any(mod == f or mod.startswith(f + ".") for f in _FORBIDDEN), (
                f"{path} imports forbidden module {mod!r}"
            )
