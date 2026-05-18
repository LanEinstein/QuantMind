"""X-018 — Phase X import-isolation tests (P2-2 §2 red line 17).

Every Phase X self-evolution module — i.e. everything under
``backend/evolution/`` plus the 8 self-evolution helpers in
``backend/services/`` — is FORBIDDEN from importing the seven
decision-path subpackages:

* ``backend.api``       (P1-5 §2 — write surface)
* ``backend.broker``    (P0-5 / P1-2.A — mirror state)
* ``backend.risk``      (P0-7 — risk engine isolation)
* ``backend.llm``       (P0-10 — LLM-layer concern)
* ``backend.agents``    (P0-10 — agent-layer concern)
* ``backend.mirofish``  (P0-8 — MiroFish is decision evidence)
* ``backend.data``      (P0-8 — data-layer concern)

The same boundary is enforced by:

1. ``scripts/redline-check.sh`` — grep-cum-AST gate the pre-commit /
   CI hook invokes.
2. This test file — AST scan that runs inside the regular pytest pass
   so the redline failure surfaces during normal development.
3. ``pyproject.toml`` ``[tool.ruff.lint.flake8-tidy-imports.banned-api]``
   rules — provide lint-time feedback the moment a developer adds a
   forbidden ``from backend.<x>`` line.

The three layers are intentionally redundant: a determined contributor
could disable one, but disabling all three requires editing three
different files and three different review comments — which makes the
red-line violation extremely visible in code review.

If this test fails the fix is almost never to weaken the test — the
fix is to refactor the offending Phase X module so the dependency
flows the right direction (Phase X depends on cost_guard / audit /
shadow_chain primitives; the decision-path side never imports Phase X).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

EVOLUTION_PACKAGE = REPO_ROOT / "backend" / "evolution"

PHASE_X_SERVICE_FILES: tuple[Path, ...] = tuple(
    REPO_ROOT / "backend" / "services" / name
    for name in (
        "prompt_registry.py",
        "shadow_chain.py",
        "exemplar_selector.py",
        "dspy_gepa_runner.py",
        "evolution_dispatcher.py",
        "amendment_drafter.py",
        "evolution_feishu_notifier.py",
        "evolution_audit_writer.py",
    )
)
"""8 self-evolution services. Locked here so a future ``cost_guard.py``
refactor cannot accidentally drop one without re-running the gate."""

FORBIDDEN_SUBPACKAGES: frozenset[str] = frozenset(
    {"api", "broker", "risk", "llm", "agents", "mirofish", "data"}
)
"""Seven decision-path subpackages Phase X must not import (P2-2 §2
red line 17). The Phase X / decision-path boundary is one-directional:
Phase X may depend on ``backend.audit`` + ``backend.services.cost_guard``
(both substrate, both isolated by their own redlines), but the seven
listed packages above must remain unaware of Phase X."""


def _iter_phase_x_files() -> Iterator[Path]:
    """Yield every Phase X ``*.py`` file the gate must cover."""
    yield from sorted(EVOLUTION_PACKAGE.rglob("*.py"))
    yield from PHASE_X_SERVICE_FILES


# Snapshot enumerated at import time so the size assertions stay stable
# inside a single test session.
_PHASE_X_FILES: tuple[Path, ...] = tuple(_iter_phase_x_files())


def _is_forbidden_module(module_name: str) -> bool:
    """``True`` if importing ``module_name`` crosses the Phase X boundary."""
    parts = module_name.split(".") if module_name else []
    return (
        len(parts) >= 2
        and parts[0] == "backend"
        and parts[1] in FORBIDDEN_SUBPACKAGES
    )


def _display_path(path: Path) -> str:
    """Repo-relative display path; falls back to bare path under tmp_path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _is_forbidden_top(name: str) -> bool:
    """``True`` if ``name`` itself is one of the seven forbidden subpackages.

    Used to catch package-relative imports where ``ast.ImportFrom.module``
    is only the subpackage name (``api`` / ``risk`` / ...) and the
    ``backend.`` prefix has been dropped by the relative-import resolver
    (codex review P2 cycle 1).
    """
    parts = name.split(".") if name else []
    return bool(parts) and parts[0] in FORBIDDEN_SUBPACKAGES


def _scan_imports(path: Path) -> list[str]:
    """Return import descriptions that violate the Phase X boundary."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover — surfaces test failure
        return [f"{_display_path(path)}: SyntaxError: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            # Absolute form: ``from backend.api import x``
            if _is_forbidden_module(module):
                violations.append(
                    f"{_display_path(path)}:{node.lineno}: "
                    f"from {module} import ..."
                )
            # ``from backend import api`` form
            if module == "backend":
                for alias in node.names:
                    if alias.name in FORBIDDEN_SUBPACKAGES:
                        violations.append(
                            f"{_display_path(path)}:{node.lineno}: "
                            f"from backend import {alias.name}"
                        )
            # Package-relative form: ``from ..api import router`` —
            # ``node.module`` is just ``api`` and ``node.level`` is
            # non-zero, so the ``backend.``-prefix check above misses it.
            # Codex P2 cycle 1: explicitly reject these.
            if level > 0 and _is_forbidden_top(module):
                violations.append(
                    f"{_display_path(path)}:{node.lineno}: "
                    f"from {'.' * level}{module} import ..."
                )
            # Relative ``from .. import api`` form — ``module`` empty +
            # level > 0; the alias names themselves are the subpackage.
            if level > 0 and not module:
                for alias in node.names:
                    if alias.name in FORBIDDEN_SUBPACKAGES:
                        violations.append(
                            f"{_display_path(path)}:{node.lineno}: "
                            f"from {'.' * level} import {alias.name}"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name):
                    violations.append(
                        f"{_display_path(path)}:{node.lineno}: "
                        f"import {alias.name}"
                    )
    return violations


# -----------------------------------------------------------------------------
# Locked constants
# -----------------------------------------------------------------------------


class TestPhaseXModuleEnumeration:
    def test_phase_x_files_discovered(self) -> None:
        # The X-018 gate must cover at least the 15 evolution files +
        # 8 evolution services that ship with Phase X (snapshot total
        # ≥ 23). If a future cleanup deletes one the gate stays valid
        # as long as the directory still has Phase X content; this
        # assertion is a smoke-test that the discovery walk found
        # something at all.
        assert len(_PHASE_X_FILES) >= 20

    def test_evolution_package_covered(self) -> None:
        # Every *.py under backend/evolution must end up in the snapshot.
        files_in_walk = set(_PHASE_X_FILES)
        for fp in EVOLUTION_PACKAGE.rglob("*.py"):
            assert fp in files_in_walk

    def test_all_named_services_exist(self) -> None:
        for fp in PHASE_X_SERVICE_FILES:
            assert fp.exists(), f"named Phase X service missing: {fp}"

    def test_forbidden_subpackages_locked(self) -> None:
        assert FORBIDDEN_SUBPACKAGES == {
            "api",
            "broker",
            "risk",
            "llm",
            "agents",
            "mirofish",
            "data",
        }


# -----------------------------------------------------------------------------
# AST-level import gate
# -----------------------------------------------------------------------------


class TestPhaseXImportGate:
    @pytest.mark.parametrize("path", _PHASE_X_FILES, ids=lambda p: p.name)
    def test_no_forbidden_imports(self, path: Path) -> None:
        violations = _scan_imports(path)
        assert violations == [], (
            f"Phase X module {path.relative_to(REPO_ROOT)} imports a "
            f"forbidden decision-path subpackage; "
            f"violations:\n  " + "\n  ".join(violations)
        )

    def test_synthetic_forbidden_import_detected(self, tmp_path: Path) -> None:
        # Self-check: confirm the scanner catches an actual violation
        # so a future refactor that breaks the AST walk cannot silently
        # turn the gate into a no-op.
        bad = tmp_path / "fake_phase_x.py"
        bad.write_text(
            "from backend.api.router import x\n"
            "from backend.risk import engine\n"
            "import backend.llm.fallback\n",
            encoding="utf-8",
        )
        violations = _scan_imports(bad)
        assert len(violations) == 3
        joined = "\n".join(violations)
        assert "backend.api.router" in joined
        assert "backend.risk" in joined
        assert "backend.llm.fallback" in joined

    def test_relative_import_detection(self, tmp_path: Path) -> None:
        # The AST walk also catches ``from backend import api`` shape
        # so a contributor cannot dodge the gate by re-exporting.
        bad = tmp_path / "fake_phase_x_relative.py"
        bad.write_text(
            "from backend import api as a, broker as b\n",
            encoding="utf-8",
        )
        violations = _scan_imports(bad)
        assert len(violations) == 2

    def test_package_relative_dotted_form_detected(
        self, tmp_path: Path
    ) -> None:
        # Codex P2 cycle 1: ``from ..api import router`` has
        # ``ast.ImportFrom.module == "api"`` and ``level == 2`` — the
        # prefix-only check missed it before this fix. After the fix
        # the scanner must reject every relative-form subpackage.
        bad = tmp_path / "fake_phase_x_package_relative.py"
        bad.write_text(
            "from ..api import router\n"
            "from ..risk import engine\n"
            "from ...llm import fallback\n",
            encoding="utf-8",
        )
        violations = _scan_imports(bad)
        assert len(violations) == 3
        joined = "\n".join(violations)
        assert "..api" in joined
        assert "..risk" in joined
        assert "...llm" in joined

    def test_package_relative_from_empty_module(
        self, tmp_path: Path
    ) -> None:
        # ``from .. import api, broker`` — empty module + level > 0.
        bad = tmp_path / "fake_phase_x_relative_empty.py"
        bad.write_text(
            "from .. import api, broker\n",
            encoding="utf-8",
        )
        violations = _scan_imports(bad)
        assert len(violations) == 2

    def test_relative_import_to_safe_subpackage_allowed(
        self, tmp_path: Path
    ) -> None:
        # ``from ..services import cost_guard`` — services is on the
        # allow-list (cost-guard substrate), so the scanner should
        # NOT flag a relative-to-services import. Sanity-check that
        # the new branch doesn't over-fire.
        good = tmp_path / "fake_phase_x_relative_services.py"
        good.write_text(
            "from ..services import cost_guard\n"
            "from ..audit import store\n",
            encoding="utf-8",
        )
        violations = _scan_imports(good)
        assert violations == []


# -----------------------------------------------------------------------------
# Pyproject lint integration (layer 1 of the 3-layer gate)
# -----------------------------------------------------------------------------


class TestRuffBannedApiConfig:
    """Verify ``pyproject.toml`` carries the Phase X banned-api rule.

    The lint feedback is the developer's earliest signal — by the time
    pytest runs the AST scan above the import already exists. The lint
    rule trips at the moment the line is typed in an editor with a
    ruff LSP attached.
    """

    @staticmethod
    def _pyproject_text() -> str:
        path = REPO_ROOT / "pyproject.toml"
        return path.read_text(encoding="utf-8")

    def test_banned_api_section_present(self) -> None:
        text = self._pyproject_text()
        assert "[tool.ruff.lint.flake8-tidy-imports.banned-api]" in text

    @pytest.mark.parametrize("subpkg", sorted(FORBIDDEN_SUBPACKAGES))
    def test_each_subpackage_banned(self, subpkg: str) -> None:
        text = self._pyproject_text()
        marker = f'"backend.{subpkg}"'
        assert marker in text, (
            f"banned-api rule for backend.{subpkg} missing from "
            f"pyproject.toml — X-018 layer-1 gate degraded"
        )

    def test_tidy_imports_rule_selected(self) -> None:
        text = self._pyproject_text()
        # ruff TID class encodes the flake8-tidy-imports rules.
        assert '"TID"' in text or "TID" in text


# -----------------------------------------------------------------------------
# redline-check.sh integration (layer 3 of the 3-layer gate)
# -----------------------------------------------------------------------------


class TestRedlineCheckPhaseXSection:
    @staticmethod
    def _redline_text() -> str:
        path = REPO_ROOT / "scripts" / "redline-check.sh"
        return path.read_text(encoding="utf-8")

    def test_phase_x_section_present(self) -> None:
        text = self._redline_text()
        assert "[X-018]" in text or "Phase X import" in text

    def test_forbidden_subpackages_listed(self) -> None:
        text = self._redline_text()
        for subpkg in FORBIDDEN_SUBPACKAGES:
            assert subpkg in text
