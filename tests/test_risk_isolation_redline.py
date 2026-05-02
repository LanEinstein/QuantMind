"""Regression lock for the SSoT ``backend/risk/`` import redline.

CLAUDE.md §6 declares: ``backend/risk/`` must never import
``backend.llm`` / ``backend.agents`` / ``backend.mirofish`` (or
``backend.services``, the LLM-coupled service layer). The static
``grep`` check in §5.5 of the SSoT validates direct imports — but
codex P5B-shadow R5 HIGH surfaced a TRANSITIVE violation: a fresh
``import backend.risk.engine`` triggered Python to execute
``backend/data/__init__.py``, which used to eagerly import
``DataScheduler`` → ``backend.llm.cost_tracker``.

This test imports ``backend.risk.engine`` in a clean subprocess and
asserts ``sys.modules`` does not contain any of the forbidden
namespaces. A regression that re-introduces eager ``backend.llm``
loading on the risk path will fail here loudly instead of silently
re-opening the rules-vs-LLM coupling that the redline exists to
prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_PROBE_SCRIPT = r"""
import json
import sys
import backend.risk.engine  # noqa: F401
import backend.risk.circuit_breaker  # noqa: F401
import backend.risk.stop_loss  # noqa: F401

forbidden_prefixes = (
    "backend.llm",
    "backend.agents",
    "backend.mirofish",
    "backend.services",
)
hits = sorted(
    name
    for name in sys.modules
    if any(name.startswith(p) for p in forbidden_prefixes)
)
print(json.dumps(hits))
"""


@pytest.mark.unit
def test_risk_engine_does_not_load_forbidden_modules() -> None:
    """A fresh interpreter import of backend.risk.* loads no LLM kit."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    )
    hits = json.loads(proc.stdout.strip() or "[]")
    assert hits == [], (
        "backend/risk/ silently loaded forbidden modules: "
        f"{hits}. SSoT redline (CLAUDE.md §6) requires risk to "
        "stay isolated from backend.llm / backend.agents / "
        "backend.mirofish / backend.services."
    )
