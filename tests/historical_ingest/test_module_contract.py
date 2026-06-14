"""AE-001 module contract: offline isolation.

The bulk historical ingest is an **offline batch** (amendment §4 red line 1):
it must never be wired into the 13 runtime crons nor the realtime data path,
and — like the other quant data modules — must never import the LLM / agents /
mirofish stacks (it is pure ingestion). These are AST / source contracts so a
later accidental wiring fails the suite, not just review.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path("backend/data/historical_ingest")
_FORBIDDEN_IMPORT_ROOTS = {
    "backend.llm",
    "backend.agents",
    "backend.agents_team",
    "backend.mirofish",
}


def _module_files() -> list[Path]:
    return sorted(_PKG.glob("*.py"))


def test_no_llm_agents_mirofish_imports() -> None:
    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == root or name.startswith(root + ".")
                    for root in _FORBIDDEN_IMPORT_ROOTS
                ):
                    offenders.append(f"{path.name}: imports {name}")
    assert not offenders, offenders


def test_not_wired_into_runtime_scheduler_or_crons() -> None:
    """The data scheduler and the app cron registration must not reference it."""
    runtime_sources = [
        Path("backend/data/scheduler.py"),
        Path("backend/main.py"),
    ]
    offenders: list[str] = []
    for src in runtime_sources:
        if not src.exists():
            continue
        text = src.read_text(encoding="utf-8")
        if "historical_ingest" in text:
            offenders.append(str(src))
    assert not offenders, (
        f"offline ingest must not be wired into runtime: {offenders}"
    )
