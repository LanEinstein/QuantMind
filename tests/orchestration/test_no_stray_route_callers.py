"""No-double-execution guard (U-B2, Codex P0 #4/#5).

``SimulationExecutor.route`` auto-fills the MockBroker. If any production
path other than :class:`RouteCoordinator` could call it, a plan dispatched
to the owner via Feishu (feishu_interactive) could ALSO be auto-filled in
simulation — the exact double-execution the mutual-exclusion router exists
to prevent.

This test AST-scans every production module under ``backend/`` and asserts
that the *only* file invoking ``<x>.route(...)`` is
``backend/orchestration/route_coordinator.py``. Tests are excluded (they
exercise SimulationExecutor.route directly by design). ``def route`` /
``async def route`` definitions are not calls and are not flagged.
"""

from __future__ import annotations

import ast
import pathlib

_ALLOWED = {pathlib.Path("backend/orchestration/route_coordinator.py")}


def _files_calling_dot_route() -> list[str]:
    offenders: list[str] = []
    for py in sorted(pathlib.Path("backend").rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "route"
            ):
                if py not in _ALLOWED:
                    offenders.append(f"{py}:{node.lineno}")
                break
    return offenders


def test_simulation_executor_route_has_no_stray_production_callers():
    offenders = _files_calling_dot_route()
    assert offenders == [], (
        "SimulationExecutor.route() must only be invoked by RouteCoordinator "
        "(no-double-execution red line, U-B2). Stray '.route(' callers: "
        f"{offenders}"
    )
