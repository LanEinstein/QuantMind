"""AC-001 / AC-007 — backend/style import-isolation contract (AST).

The style module is a pure, deterministic classifier: the value-line decision is
computed from PIT features + the human-pinned theme artifact, never from LLM
text. It must never import the LLM / agents / mirofish / data stacks, nor
construct an InstructionPlan. An AST scan (not a grep) so a refactor cannot
weaken the guard.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STYLE = _ROOT / "backend" / "style"
_FORBIDDEN = {"llm", "agents", "agents_team", "mirofish", "data"}


def _forbidden_imports(root: pathlib.Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        len(parts) >= 2
                        and parts[0] == "backend"
                        and parts[1] in _FORBIDDEN
                    ):
                        violations.append(
                            f"{path.name}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                parts = mod.split(".")
                if (
                    len(parts) >= 2
                    and parts[0] == "backend"
                    and parts[1] in _FORBIDDEN
                ):
                    violations.append(f"{path.name}:{node.lineno}: from {mod}")
                # `from backend import llm`
                if mod == "backend":
                    for alias in node.names:
                        if alias.name in _FORBIDDEN:
                            violations.append(
                                f"{path.name}:{node.lineno}: from import {alias.name}"
                            )
    return violations


def test_style_module_exists() -> None:
    """Guard against an empty dir silently passing the isolation scan."""
    assert (_STYLE / "classifier.py").is_file()
    assert (_STYLE / "models.py").is_file()


def test_no_forbidden_backend_imports() -> None:
    assert _forbidden_imports(_STYLE) == []


def test_no_instruction_plan_construction() -> None:
    """The style module is advisory data — it never builds an order."""
    for path in sorted(_STYLE.rglob("*.py")):
        assert "InstructionPlan(" not in path.read_text(encoding="utf-8"), path


def test_scanner_catches_planted_import(tmp_path: pathlib.Path) -> None:
    """Self-test: the AST scanner actually flags a forbidden import."""
    planted = tmp_path / "evil.py"
    planted.write_text("from backend.llm import client\n", encoding="utf-8")
    assert _forbidden_imports(tmp_path)


@pytest.mark.parametrize(
    "snippet",
    [
        "import backend.agents\n",
        "from backend import mirofish\n",
        "import backend.data.x\n",
    ],
)
def test_scanner_catches_all_import_forms(
    tmp_path: pathlib.Path, snippet: str
) -> None:
    (tmp_path / "p.py").write_text(snippet, encoding="utf-8")
    assert _forbidden_imports(tmp_path)
