#!/usr/bin/env python
"""Read-only account-lines API for the frontend panel (post-MI-1 unit).

A standalone FastAPI app — deliberately NOT ``backend.main`` (the sealed
M4 dual-line runtime with Mongo/Redis/schedulers stays dormant). One GET
endpoint, no writes, no auth: it binds 127.0.0.1 only and the frontend
reaches it through the Vite ``/api`` proxy (``frontend/vite.config.ts``
targets ``localhost:8001``); SSH tunnel is the remote boundary.

Response = the frontend envelope ``{"status", "data", "error"}`` with
``data`` = ``scripts/account_view.py --json`` shape + the newest ledger
rows (append order) + the monthly execution-drift disclosure.

Usage (from the repository root — ledger paths are repo-relative)::

    python scripts/account_api.py                 # 127.0.0.1:8001
    python scripts/account_api.py --port 8001
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from backend.portfolio.lines import account_view_payload, build_account_view
from backend.portfolio.mirror_ledger import DEFAULT_LEDGER as DEFAULT_MIRROR
from backend.portfolio.mirror_ledger import recent_rows
from backend.portfolio.z_ledger_io import DEFAULT_LEDGER as DEFAULT_Z
from scripts.mirror_drift_report import DEFAULT_ADVISORY_HISTORY, monthly_drift

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
DEFAULT_RECENT_LIMIT = 50
SHANGHAI = ZoneInfo("Asia/Shanghai")
# The trade id is an owner/LLM-minted dedupe key, not something to display.
_HIDDEN_ROW_FIELDS = frozenset({"external_trade_id"})


def _display_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _HIDDEN_ROW_FIELDS}


def build_lines_payload(
    *,
    mirror_path: Path,
    z_path: Path,
    history_path: Path,
    recent_limit: int,
) -> dict[str, Any]:
    """Everything the panel shows, computed fresh from the ledgers."""
    view = build_account_view(mirror_path, z_path)
    return {
        **account_view_payload(view),
        "recent_ledger_rows": [
            _display_row(r) for r in recent_rows(mirror_path, recent_limit)
        ],
        "monthly_drift": monthly_drift(mirror_path, history_path),
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    }


def create_app(
    *,
    mirror_path: Path = DEFAULT_MIRROR,
    z_path: Path = DEFAULT_Z,
    history_path: Path = Path(DEFAULT_ADVISORY_HISTORY),
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> FastAPI:
    app = FastAPI(title="QuantMind account lines (read-only)", docs_url=None)

    @app.get("/api/portfolio/lines")
    def lines() -> dict[str, Any]:  # sync: small local file reads
        try:
            data = build_lines_payload(
                mirror_path=mirror_path,
                z_path=z_path,
                history_path=history_path,
                recent_limit=recent_limit,
            )
        except (ValueError, OSError) as exc:
            # A broken/unreplayable ledger is a real condition the owner
            # must see on the panel (MirrorDriftError is a ValueError).
            return {"status": "error", "data": None, "error": str(exc)}
        return {"status": "ok", "data": data, "error": None}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mirror", default=str(DEFAULT_MIRROR))
    parser.add_argument("--z-ledger", default=str(DEFAULT_Z))
    parser.add_argument("--history", default=DEFAULT_ADVISORY_HISTORY)
    parser.add_argument("--recent-limit", type=int, default=DEFAULT_RECENT_LIMIT)
    args = parser.parse_args()

    import uvicorn

    app = create_app(
        mirror_path=Path(args.mirror),
        z_path=Path(args.z_ledger),
        history_path=Path(args.history),
        recent_limit=args.recent_limit,
    )
    uvicorn.run(app, host=BIND_HOST, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
