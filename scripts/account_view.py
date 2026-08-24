#!/usr/bin/env python
"""MI-1 line-split account view — R (sleeve mirror) / Z (rent) / cash.

Local read-only display of :func:`backend.portfolio.lines.build_account_view`.
``--json`` emits the same machine shape the account-lines API serves
(``scripts/account_api.py`` → ``GET /api/portfolio/lines``).

Usage::

    python scripts/account_view.py            # human-readable lines
    python scripts/account_view.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.portfolio.lines import (
    account_view_payload,
    build_account_view,
    render_account_lines,
)
from backend.portfolio.mirror_ledger import DEFAULT_LEDGER as DEFAULT_MIRROR
from backend.portfolio.z_ledger_io import DEFAULT_LEDGER as DEFAULT_Z


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", default=str(DEFAULT_MIRROR))
    parser.add_argument("--z-ledger", default=str(DEFAULT_Z))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    view = build_account_view(Path(args.mirror), Path(args.z_ledger))
    if args.as_json:
        payload = account_view_payload(view)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_account_lines(view))
    return 0


if __name__ == "__main__":
    sys.exit(main())
