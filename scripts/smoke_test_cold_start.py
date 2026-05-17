#!/usr/bin/env python
"""J-002 — Cold-start integration smoke test.

Drives the backend lifespan against a real (or testcontainers-style)
Mongo + Redis and asserts the 18 ``_init_orchestration_layer`` slots
plus the 6 lifespan-top slots populate cleanly. Verifies that the
``QUANTMIND_LLM_STUB`` env var is honoured by the wired
:class:`LLMRouter` so a future N-day simulator (J-005) cannot
accidentally burn real LLM budget.

Usage::

    # Default: localhost Mongo + Redis, stub LLM enabled, broker
    # replica-set gate skipped (dev environments).
    python scripts/smoke_test_cold_start.py

    # Production-style verification (still no real LLM cost):
    MONGODB_URI=mongodb://prod-replica/quantmind \\
    REDIS_URL=redis://prod-redis:6379/0 \\
        python scripts/smoke_test_cold_start.py --strict

    # Emit JSON envelope for CI dashboards.
    python scripts/smoke_test_cold_start.py --json

Exit codes:

* ``0`` — lifespan booted cleanly, all required slots populated,
  LLM router in stub mode (so 0 real LLM cost).
* ``1`` — at least one required slot missing/None, OR the lifespan
  raised, OR ``--require-stub`` was passed and the router is not
  stubbed. ``stderr`` lists every failure cause.

Red lines:

* The script defaults to ``QUANTMIND_LLM_STUB=1`` so cold-start can
  never burn real LLM budget. Override at your own risk via
  ``--allow-real-llm``.
* ``QUANTMIND_BROKER_SKIP_RS_GATE=1`` is set by default so dev Mongo
  (standalone, no replica set) does not fail the BrokerScheduler
  pre-flight. Production callers should pass ``--strict`` to require
  the replica-set gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Any

from backend.services.smoke_check import (
    ORCHESTRATION_REQUIRED_SLOTS,
    SmokeCheckResult,
    check_app_state,
    format_check_result,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smoke_test_cold_start",
        description=(
            "Cold-start smoke test — drive backend lifespan + assert "
            "the 18 orchestration slots populate; verify LLM router is "
            "in stub mode so no real LLM cost is incurred."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Production-style verification: do NOT set "
            "QUANTMIND_BROKER_SKIP_RS_GATE; require the broker scheduler "
            "replica-set gate to pass."
        ),
    )
    parser.add_argument(
        "--allow-real-llm",
        action="store_true",
        help=(
            "Allow the real LLM router to handle complete() calls during "
            "the smoke test. By default the script sets "
            "QUANTMIND_LLM_STUB=1 so the cold-start path cannot burn "
            "real LLM budget."
        ),
    )
    parser.add_argument(
        "--require-stub",
        action="store_true",
        default=True,
        help=(
            "Fail the smoke test when the LLM router is not in stub mode "
            "(default True; pair with --allow-real-llm to disable)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON envelope to stdout for CI consumption.",
    )
    return parser.parse_args(argv)


def _prepare_env(args: argparse.Namespace) -> None:
    """Apply default env tweaks the smoke test relies on.

    Codex cycle 2 P2 fix — these tweaks are now FORCE-SET (not
    setdefault) so a pre-existing ``QUANTMIND_LLM_STUB=0`` or
    ``QUANTMIND_BROKER_SKIP_RS_GATE=0`` in the parent shell cannot
    silently bypass the smoke test's "no real LLM cost" + "no replica
    set required" contracts. To opt out, the operator must pass
    ``--allow-real-llm`` or ``--strict`` explicitly.
    """
    if not args.allow_real_llm:
        os.environ["QUANTMIND_LLM_STUB"] = "1"
    if not args.strict:
        os.environ["QUANTMIND_BROKER_SKIP_RS_GATE"] = "1"


async def _run_lifespan_smoke() -> tuple[SmokeCheckResult, str | None]:
    """Boot the backend lifespan and run the smoke check.

    Returns ``(result, traceback_text)``: ``traceback_text`` is None
    when the lifespan booted cleanly; otherwise it is the formatted
    traceback so the operator can diagnose without re-running.

    Codex cycle 5 P3 fix — catch ``BaseException`` (not just
    ``Exception``) so the lifespan fail-fast gates that raise
    ``SystemExit`` (secrets validator, J-007 owner authorization,
    acceptance gate) surface as a structured failure result with
    captured traceback instead of letting SystemExit propagate
    silently out of the smoke script (especially in ``--json`` mode).
    ``KeyboardInterrupt`` is intentionally re-raised so an operator
    Ctrl-C still works.
    """
    # Late imports — main.py constructs the FastAPI app at import time,
    # which is expensive. Deferring keeps argparse + env prep cheap.
    from backend.main import app, lifespan

    try:
        async with lifespan(app):
            return check_app_state(app.state), None
    except KeyboardInterrupt:
        raise
    except BaseException:  # noqa: BLE001 — preserve SystemExit + Exception
        return (
            SmokeCheckResult(
                missing_required=ORCHESTRATION_REQUIRED_SLOTS,
                none_required=(),
                none_conditional=(),
                present_required=(),
                llm_router_stubbed=None,
            ),
            traceback.format_exc(),
        )


def _serialise_result(
    result: SmokeCheckResult,
    *,
    args: argparse.Namespace,
    traceback_text: str | None,
) -> dict[str, Any]:
    return {
        "verdict": "PASS" if result.ok else "FAIL",
        "ok": result.ok,
        "missing_required": list(result.missing_required),
        "none_required": list(result.none_required),
        "none_conditional": [
            {"slot": slot, "reason": reason}
            for slot, reason in result.none_conditional
        ],
        "present_required_count": len(result.present_required),
        "expected_required_count": len(ORCHESTRATION_REQUIRED_SLOTS),
        "llm_router_stubbed": result.llm_router_stubbed,
        "args": {
            "strict": args.strict,
            "allow_real_llm": args.allow_real_llm,
            "require_stub": args.require_stub,
        },
        "lifespan_traceback": traceback_text,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _prepare_env(args)

    result, traceback_text = asyncio.run(_run_lifespan_smoke())

    # Pair the args.require_stub flag with the actual flag — when
    # require_stub is True and the router is not stubbed, treat as
    # failure even if every slot is wired.
    stub_failure = (
        args.require_stub
        and not args.allow_real_llm
        and result.llm_router_stubbed is False
    )

    overall_ok = result.ok and traceback_text is None and not stub_failure

    if args.json:
        envelope = _serialise_result(
            result, args=args, traceback_text=traceback_text
        )
        envelope["stub_failure"] = stub_failure
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        if traceback_text is not None:
            print("smoke check verdict: FAIL", file=sys.stderr)
            print(
                "lifespan raised during cold start — traceback follows",
                file=sys.stderr,
            )
            print(traceback_text, file=sys.stderr)
        print(format_check_result(result))
        if stub_failure:
            print(
                "ERROR: --require-stub set but LLM router is not in "
                "QUANTMIND_LLM_STUB mode. Cold start cannot proceed "
                "without burning real LLM budget. Either set "
                "QUANTMIND_LLM_STUB=1 (default) or pass --allow-real-llm.",
                file=sys.stderr,
            )

    return 0 if overall_ok else 1


if __name__ == "__main__":  # pragma: no cover — exercised via tests
    raise SystemExit(main())
