"""R-002 module contract: realtime isolation of the backtest oracle.

Acceptance red line: rqalpha (the test-time differential oracle) must
NEVER enter the realtime path. Three guards, this AST scan being the
authoritative one (the redline ``[R-002]`` grep is the standalone-CI
fast gate; ruff TID251 separately bans the trading stack from being
imported BY strategy_evolution).
"""

from __future__ import annotations

import ast
import pathlib

EVOLUTION_ROOT = pathlib.Path("backend/strategy_evolution")
BACKEND_ROOT = pathlib.Path("backend")
ORACLE_MODULE = "backend.strategy_evolution.backtest_oracle"
# R-002-amendment-2026-06-14: the venv-only subprocess entry legitimately
# imports rqalpha (it runs in the oracle venv, never imported by the main env).
ENTRY_ROOT = pathlib.Path("backend/backtest/rqalpha_entry")
ENTRY_MODULE = "backend.backtest.rqalpha_entry"


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
    return found


class TestOracleRealtimeIsolation:
    def test_no_backend_module_outside_evolution_imports_oracle(
        self,
    ) -> None:
        offenders: list[str] = []
        for path in sorted(BACKEND_ROOT.rglob("*.py")):
            if EVOLUTION_ROOT in path.parents or path.parent == (
                EVOLUTION_ROOT
            ):
                continue
            imports = _imports_of(path)
            if any(name.startswith(ORACLE_MODULE) for name in imports):
                offenders.append(str(path))
        assert offenders == [], (
            f"realtime path imports the backtest oracle: {offenders}"
        )

    def test_rqalpha_import_confined_to_allowlist(self) -> None:
        # R-002 allowlist (amendment 2026-06-14): the oracle adapter +
        # the venv-only subprocess entry. Nothing else may name rqalpha.
        offenders: list[str] = []
        for path in sorted(BACKEND_ROOT.rglob("*.py")):
            imports = _imports_of(path)
            if any(
                name == "rqalpha" or name.startswith("rqalpha.")
                for name in imports
            ):
                if path == EVOLUTION_ROOT / "backtest_oracle.py":
                    continue
                if ENTRY_ROOT in path.parents:
                    continue
                offenders.append(str(path))
        assert offenders == [], (
            f"rqalpha imported outside the R-002 allowlist: {offenders}"
        )

    def test_no_main_env_module_imports_the_venv_entry(self) -> None:
        """The entry runs ONLY as a subprocess (``python -m rqalpha_entry``).

        If any main-env module imported ``backend.backtest.rqalpha_entry`` (or
        the top-level ``rqalpha_entry``), pytest collection would drag rqalpha
        into the main env — which has no rqalpha — and the import would crash.
        """
        offenders: list[str] = []
        for path in sorted(BACKEND_ROOT.rglob("*.py")):
            if ENTRY_ROOT in path.parents:
                continue
            imports = _imports_of(path)
            if any(
                name == ENTRY_MODULE
                or name.startswith(ENTRY_MODULE + ".")
                or name == "rqalpha_entry"
                or name.startswith("rqalpha_entry.")
                for name in imports
            ):
                offenders.append(str(path))
        assert offenders == [], (
            f"main-env module imports the venv-only entry: {offenders}"
        )

    def test_rqalpha_import_in_adapter_is_lazy(self) -> None:
        """A module-level import would crash boot when the optional dep
        is absent — the adapter must import inside the run method."""
        tree = ast.parse(
            (EVOLUTION_ROOT / "backtest_oracle.py").read_text(
                encoding="utf-8"
            )
        )
        for node in tree.body:  # module level only
            assert not (
                isinstance(node, ast.Import)
                and any(a.name == "rqalpha" for a in node.names)
            ), "rqalpha must be lazily imported"
            assert not (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("rqalpha")
            ), "rqalpha must be lazily imported"

    def test_no_vendored_rqalpha_code(self) -> None:
        """NOASSERTION license discipline: depend, never vendor."""
        vendored = [
            str(p)
            for p in BACKEND_ROOT.rglob("*")
            if p.is_dir() and p.name == "rqalpha"
        ]
        assert vendored == []
