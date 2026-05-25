"""No-double-execution guard (U-B2, Codex P0 #4/#5; refined U-C1).

``SimulationExecutor.route`` auto-fills the MockBroker. If any production
path other than :class:`RouteCoordinator` could call it, a plan dispatched
to the owner via Feishu (feishu_interactive) could ALSO be auto-filled in
simulation — the exact double-execution the mutual-exclusion router exists
to prevent.

The invariant: **``SimulationExecutor.route`` is invoked only by
RouteCoordinator.** To call ``SimulationExecutor.route`` a module must hold
a ``SimulationExecutor`` instance, which means it must import the
``simulation_executor`` module (the only place that type is defined +
constructed). So this test AST-scans every production ``backend/`` module
that imports ``simulation_executor`` and asserts the only one invoking
``<x>.route(...)`` is ``route_coordinator.py``.

Why the import gate (U-C1): the Line-1/Line-2 production *runners* call
``RouteCoordinator.route`` — the single legitimate routing edge. They are
import-clean (no ``backend.services.simulation_executor`` import, enforced
by the orchestration isolation lint), so they can hold no SimulationExecutor
and their ``coordinator.route`` call is not a stray executor call. Gating on
the import keeps the guard precise: it still catches any module that pulls in
SimulationExecutor and calls ``.route``, while not mis-flagging a runner that
merely routes through the coordinator. ``def route`` definitions are not
calls and are not flagged.
"""

from __future__ import annotations

import ast
import pathlib

_ALLOWED = {pathlib.Path("backend/orchestration/route_coordinator.py")}
_EXECUTOR_MODULE = "simulation_executor"


def _imports_simulation_executor(tree: ast.AST) -> bool:
    """True iff the module imports the ``simulation_executor`` module.

    Holding a ``SimulationExecutor`` instance (the only way to call its
    ``.route``) requires importing the module that defines it. Comments /
    docstrings mentioning the name are NOT imports, so they never trip this.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from backend.services.simulation_executor import X` → module tail
            # is the executor module; `from backend.services import
            # simulation_executor` (and relative `from . import
            # simulation_executor`) → the module is imported as a NAME, so the
            # alias list must be inspected too, or the gate has a bypass
            # (Codex U-C1 P2). `node.module` is None for `from . import …`.
            if node.module and node.module.split(".")[-1] == _EXECUTOR_MODULE:
                return True
            if any(
                alias.name.split(".")[-1] == _EXECUTOR_MODULE for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[-1] == _EXECUTOR_MODULE for alias in node.names
            ):
                return True
    return False


def _calls_dot_route(tree: ast.AST) -> int | None:
    """Return the lineno of the first ``<x>.route(...)`` call, else None."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "route"
        ):
            return node.lineno
    return None


def _files_calling_dot_route() -> list[str]:
    offenders: list[str] = []
    for py in sorted(pathlib.Path("backend").rglob("*.py")):
        if py in _ALLOWED:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # A module that does not import simulation_executor cannot hold a
        # SimulationExecutor to call .route on (it can only route through an
        # injected RouteCoordinator) — skip it.
        if not _imports_simulation_executor(tree):
            continue
        lineno = _calls_dot_route(tree)
        if lineno is not None:
            offenders.append(f"{py}:{lineno}")
    return offenders


def test_simulation_executor_route_has_no_stray_production_callers():
    offenders = _files_calling_dot_route()
    assert offenders == [], (
        "SimulationExecutor.route() must only be invoked by RouteCoordinator "
        "(no-double-execution red line, U-B2). A module that imports "
        "simulation_executor and calls '.route(' is a stray caller: "
        f"{offenders}"
    )


def test_guard_flags_a_planted_stray_caller():
    """The guard must catch a module that imports the executor + calls .route.

    Proves the refinement did not neuter the check: a planted stray caller
    (import + ``executor.route(...)``) is detected, while an import-clean
    coordinator.route caller is not.
    """
    stray = ast.parse(
        "from backend.services.simulation_executor import SimulationExecutor\n"
        "async def go(ex):\n    await ex.route(plan, now=None)\n"
    )
    assert _imports_simulation_executor(stray)
    assert _calls_dot_route(stray) is not None

    # The `from backend.services import simulation_executor` form imports the
    # executor module as a NAME, not via node.module — the gate must still
    # catch it (Codex U-C1 P2; no import-style bypass).
    stray_module_alias = ast.parse(
        "from backend.services import simulation_executor\n"
        "async def go():\n    await simulation_executor.SimulationExecutor().route(p)\n"
    )
    assert _imports_simulation_executor(stray_module_alias)
    # Plain `import backend.services.simulation_executor` too.
    stray_plain = ast.parse("import backend.services.simulation_executor\n")
    assert _imports_simulation_executor(stray_plain)

    clean = ast.parse(
        "from backend.orchestration.route_coordinator import RouteCoordinator\n"
        "async def go(coord):\n    await coord.route(signal, now=None)\n"
    )
    assert not _imports_simulation_executor(clean)  # no executor import → skipped
