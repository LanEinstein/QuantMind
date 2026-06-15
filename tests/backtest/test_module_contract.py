"""AE-003 module contract: backend/backtest/ import isolation (amendment §2.1).

The harness replays no LLM (P1) and never touches the live mirror, so it must
not import backend.{llm,agents,agents_team,mirofish,api,broker}. The only
permitted strategy_evolution import is the broker-free ``harsh_fill_model``.
These are AST contracts so an accidental wiring fails the suite, not just review
(mirrors strategy_evolution's existing contract).
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path("backend/backtest")
_FORBIDDEN_ROOTS = {
    "backend.llm",
    "backend.agents",
    "backend.agents_team",
    "backend.mirofish",
    "backend.api",
    "backend.broker",
}
_FORBIDDEN_LEAVES = {"llm", "agents", "agents_team", "mirofish", "api", "broker"}


def _abs_targets(source: str) -> list[str]:
    """Absolute dotted import targets, expanding ``from X import a`` to ``X.a``.

    Relative imports (``from ..broker import x`` / ``from . import broker``) are
    emitted as a ``rel:<leaf>`` marker per component so the leaf-name guard can
    catch them — covering the bypasses codex flagged (cycle-2 P2).
    """
    targets: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            if node.level == 0 and node.module:
                targets.append(node.module)
                targets += [f"{node.module}.{n}" for n in names]
            else:  # relative — guard by component leaf names
                comps = (node.module.split(".") if node.module else []) + names
                targets += [f"rel:{c}" for c in comps]
    return targets


def _all_targets() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(_PKG.glob("*.py")):
        out += [
            (path.name, t)
            for t in _abs_targets(path.read_text(encoding="utf-8"))
        ]
    return out


def _is_forbidden(target: str) -> bool:
    if target.startswith("rel:"):
        return target[len("rel:"):] in _FORBIDDEN_LEAVES
    return any(
        target == root or target.startswith(root + ".")
        for root in _FORBIDDEN_ROOTS
    )


def test_no_forbidden_stack_imports() -> None:
    offenders = [f"{f}: {t}" for f, t in _all_targets() if _is_forbidden(t)]
    assert not offenders, offenders


def test_forbidden_import_detector_catches_all_forms() -> None:
    # Positive controls for the bypass forms codex flagged.
    assert any(_is_forbidden(t) for t in _abs_targets("import backend.broker"))
    assert any(
        _is_forbidden(t) for t in _abs_targets("from backend.broker import X")
    )
    assert any(
        _is_forbidden(t) for t in _abs_targets("from backend import broker")
    )
    assert any(
        _is_forbidden(t) for t in _abs_targets("from ..broker import X")
    )
    assert any(
        _is_forbidden(t) for t in _abs_targets("from . import broker")
    )
    assert not any(
        _is_forbidden(t)
        for t in _abs_targets("from backend.candidate_selector import sel")
    )


def test_strategy_evolution_only_harsh_fill_model() -> None:
    se = "backend.strategy_evolution"
    offenders = [
        f"{f}: {t}"
        for f, t in _all_targets()
        if (t == se or t.startswith(se + "."))
        and not t.startswith(se + ".harsh_fill_model")
    ]
    assert not offenders, offenders


# -- decision_compare lint (AST authoritative) -------------------------


def _bare_float_compares(tree: ast.AST) -> list[int]:
    """Line numbers of comparisons with a float-literal operand."""
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                if isinstance(operand, ast.Constant) and isinstance(
                    operand.value, float
                ):
                    out.append(node.lineno)
    return out


def test_no_bare_float_threshold_comparison() -> None:
    offenders: list[str] = []
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.name}:{ln}" for ln in _bare_float_compares(tree)]
    assert not offenders, (
        f"bare float threshold comparison(s) — use decision_compare: {offenders}"
    )


def test_bare_float_detector_rejects_synthetic() -> None:
    # Positive control: the detector must flag a bare float comparison.
    bad = ast.parse("def f(x):\n    return x < 0.5\n")
    assert _bare_float_compares(bad)
    good = ast.parse("def f(x):\n    return decision_compare(x, gate, '<')\n")
    assert not _bare_float_compares(good)


# -- qlib Ref look-ahead lint (AST authoritative) ----------------------


def _lookahead_refs(tree: ast.AST) -> list[int]:
    """Line numbers of ``Ref(expr, <negative>)`` calls (forward look = bug)."""
    out: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Ref"
        ):
            for arg in node.args:
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    out.append(node.lineno)
                elif (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, (int, float))
                    and arg.value < 0
                ):
                    out.append(node.lineno)
    return out


def test_no_lookahead_ref() -> None:
    offenders: list[str] = []
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.name}:{ln}" for ln in _lookahead_refs(tree)]
    assert not offenders, f"look-ahead Ref(...): {offenders}"


def test_lookahead_ref_detector_rejects_synthetic() -> None:
    assert _lookahead_refs(ast.parse("y = Ref(close, -1)\n"))
    assert not _lookahead_refs(ast.parse("y = Ref(close, 1)\n"))


# -- rqalpha_entry exclusion (R-002-amendment-2026-06-14 §2.5) -----------


def test_decision_path_lints_exclude_rqalpha_entry() -> None:
    """The venv-only entry must NOT be scanned by the deterministic-path lints.

    ``rqalpha_entry`` runs in the oracle venv (imports rqalpha, does float
    friction math) and is not part of the NEP-50-sensitive decision path. The
    ``_PKG.glob("*.py")`` used above is non-recursive, so it is naturally
    excluded — lock that here so a future switch to ``rglob`` cannot silently
    drag the entry (and its legitimate rqalpha import) into the lint.
    """
    entry = _PKG / "rqalpha_entry"
    assert entry.is_dir(), "rqalpha_entry package missing"
    assert list(entry.glob("*.py")), "rqalpha_entry has no modules"
    # The scan source is _PKG.glob("*.py") (non-recursive): every scanned path
    # is a direct child of backend/backtest, never under rqalpha_entry/.
    scanned_paths = list(_PKG.glob("*.py"))
    assert all(p.parent == _PKG for p in scanned_paths)
    assert all("rqalpha_entry" not in p.parts for p in scanned_paths)


def test_pit_export_backend_data_import_is_allowed() -> None:
    """pit_export legitimately imports backend.data (the [BACKTEST] allowlist
    forbids only llm/agents/api/broker) — assert it is NOT flagged."""
    targets = [t for f, t in _all_targets() if f == "pit_export.py"]
    assert targets, "pit_export.py not scanned"
    assert not any(_is_forbidden(t) for t in targets)
