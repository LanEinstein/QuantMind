"""Sleeve push state — shared between the push script and reconciliation.

The push script (``scripts/push_sleeve_advisory.py``) persists here what was
last DELIVERED; the reconciliation loop clears the ``awaiting_report`` block
once the owner reports execution (or an explicit decision not to follow).
While ``awaiting_report`` is set, the nightly cron re-pushes the advisory
(the plan's "no report by next close → assume unfilled, recompute and
re-push"); a failed send never mutates this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PUSH_STATE = Path("data/factor_research/sleeve_push_state.json")
AWAITING_KEY = "awaiting_report"


def load_push_state(path: Path) -> dict[str, Any]:
    """Last-delivered push state; empty dict = never pushed (will announce)."""
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # unreadable state → behaves as first run (dedupe only)
    return loaded if isinstance(loaded, dict) else {}


def save_push_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def clear_awaiting_report(path: Path) -> bool:
    """Drop the pending-execution flag (owner reported / decided).

    Returns True when a flag was actually cleared. Called by the
    reconciliation loop after a booked fill or an explicit no-action.
    """
    state = load_push_state(path)
    if AWAITING_KEY not in state:
        return False
    save_push_state(path, {k: v for k, v in state.items() if k != AWAITING_KEY})
    return True
