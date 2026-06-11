"""theme_research module isolation (Y-005).

The theme-research layer is the ONLY LLM+web-bearing module of the stack, but it
reaches LLM/web exclusively through INJECTED Protocols — it must never hard-import
the trading stack, and the deterministic 0-LLM modules (screening / marketdata /
risk / cost-guard) must never import it back (so their 0-LLM posture is preserved
by construction, P0-8-amendment-2026-06-01 §3).
"""

from __future__ import annotations

import ast
import pathlib

# theme_research may use backend.models (pure models), backend.strategy_evolution
# (LiveArtifactRegistry), backend.knowledge_graph (pinned KG). It must NOT import
# any of these (LLM/web arrive via injected Protocols; trading stack stays out).
_FORBIDDEN = {
    "api",
    "broker",
    "risk",
    "llm",
    "agents",
    "mirofish",
    "data",
    "screening",
    "marketdata_snapshot",
}

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _backend_imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "backend."
        ):
            parts = (node.module or "").split(".")
            if len(parts) >= 2 and parts[1] in _FORBIDDEN:
                hits.append(f"{path.name}: from {node.module}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                p = a.name.split(".")
                if len(p) >= 2 and p[0] == "backend" and p[1] in _FORBIDDEN:
                    hits.append(f"{path.name}: import {a.name}")
    return hits


def test_theme_research_does_not_import_trading_stack() -> None:
    root = _ROOT / "backend/theme_research"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        violations.extend(_backend_imports(path))
    assert violations == [], violations


def test_zero_llm_modules_do_not_import_theme_research() -> None:
    """screening / marketdata_snapshot / risk must stay 0-LLM — they may not
    import the LLM-bearing theme_research layer."""
    offenders: list[str] = []
    for sub in ("screening", "marketdata_snapshot", "risk"):
        root = _ROOT / "backend" / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = ""
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                elif isinstance(node, ast.Import):
                    mod = ";".join(a.name for a in node.names)
                if "theme_research" in mod:
                    offenders.append(f"{sub}/{path.name}: {mod}")
    assert offenders == [], offenders
