"""U-D3 dry-run artifact + CLI rendering helpers.

Extracted from :mod:`scripts.dry_run_double_line` to keep that orchestration
module under the 800-line cap (CLAUDE.md §3). This module owns the *shape* of
the owner-review artifact and the CLI table/JSON envelopes — pure, side-effect-
free formatting (the single ``write_artifact`` is the only I/O) so the
double-line harness stays focused on wiring the two production lines.

The artifact is what the **owner reads** to judge signal reasonableness before
flipping ``dry_run_double_line_pass`` in ``config/pilot_readiness.yaml`` (PILOT
condition 3). The explicit ``real_sends`` / ``owner_reviewed`` / ``pass`` fields
are written by the harness and NEVER flipped by it — the owner flips
owner_reviewed/pass after review, which then justifies the manifest flip.

These helpers take the harness's ``DryRunContext`` / ``DryRunOutcome`` ducked as
``Any`` (no import back into the harness module → no import cycle); they only
read public attributes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA = "quantmind.dry_run.double_line/v1"


def count_by(values: Sequence[str]) -> dict[str, int]:
    """Tally a list of outcome strings into a ``{value: count}`` dict."""
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def build_artifact(ctx: Any, *, initial_capital: float) -> dict[str, Any]:
    """Build the dry-run PASS artifact dict (owner reads the wire texts).

    ``cost_guard_spend_rmb`` is left ``None`` here and back-filled by the caller
    after the async spend read. ``owner_reviewed`` / ``pass`` are always written
    False — the harness never self-attests a PASS.
    """
    frame = ctx.frame
    line1_outcomes = count_by([r.outcome.value for r in ctx.line1_results])
    line2_daily_routes = [
        route.outcome.value
        for r in ctx.line2_daily_results
        for route in r.sell_routes
    ]
    line2_intraday_routes = [
        route.outcome.value
        for r in ctx.line2_intraday_results
        for route in r.routes
    ]
    return {
        "schema": ARTIFACT_SCHEMA,
        "run_metadata": _run_metadata(ctx, frame, initial_capital),
        "per_line_outcomes": {
            "line1": line1_outcomes,
            "line2_daily": count_by(line2_daily_routes),
            "line2_intraday": count_by(line2_intraday_routes),
        },
        "rendered_signals": [
            {
                "line": s.line,
                "side": s.side,
                "instruction_id": s.instruction_id,
                "code": s.code,
                "wire_text": s.wire_text,
            }
            for s in ctx.collector.signals
        ],
        "rendered_count": len(ctx.collector.signals),
        "real_sends": 0,
        "real_broker_mutations": 0,
        "noop_executor_calls": ctx.executor.calls,
        "noop_dispatcher_calls": ctx.dispatcher.calls,
        "owner_reviewed": False,
        "pass": False,
    }


def _run_metadata(ctx: Any, frame: Any, initial_capital: float) -> dict[str, Any]:
    """Run-level provenance: frame checksum/snapshot id, token fingerprint, …."""
    return {
        "run_trade_date": ctx.run_trade_date,
        "frame_trade_date": ctx.frame_trade_date,
        "frame_provenance": {
            "snapshot_id": str(frame.snapshot_id),
            "vendor": frame.vendor,
            "endpoint": frame.endpoint,
            "trade_date": frame.trade_date,
            "raw_payload_sha256": frame.raw_payload_sha256,
            "size_bytes": frame.size,
            "version": frame.version,
            "parent_snapshot_ids": frame.metadata.get("parent_snapshot_ids", []),
            "fetch_time_utc": frame.fetch_time_utc.isoformat(),
        },
        "tushare_token_fingerprint": ctx.token_fingerprint or "(injected)",
        "llm_models": list(ctx.llm_models),
        "cost_guard_spend_rmb": None,  # back-filled by the caller (async read)
        "index_closes_count": len(ctx.index_closes),
        "today_instruction_count_final": ctx.today_instruction_count,
        "initial_capital_rmb": initial_capital,
    }


def write_artifact(artifact: dict[str, Any], out_path: Path) -> Path:
    """Write the artifact JSON, creating the directory; return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_path


def format_table(outcome: Any) -> str:
    """Human-readable summary table for the CLI default output."""
    verdict = "PASS" if outcome.ok else "FAIL"
    spend = (
        f"¥{outcome.cost_guard_spend_rmb:.4f}"
        if outcome.cost_guard_spend_rmb is not None
        else "n/a"
    )
    lines = [
        f"dry_run_double_line verdict: {verdict}",
        f"  run_trade_date           : {outcome.run_trade_date}",
        f"  frame_trade_date (T-1)   : {outcome.frame_trade_date}",
        f"  line1 BUY rendered       : {outcome.line1_rendered}",
        f"  line2 daily SELL rendered: {outcome.line2_daily_rendered}",
        f"  line2 intraday rendered  : {outcome.line2_intraday_rendered}",
        f"  cost_guard spend         : {spend}",
        "  real_sends               : 0  (DRY_RUN render-only)",
        f"  artifact                 : {outcome.artifact_path}",
    ]
    if outcome.errors:
        lines.append("  -- errors --")
        lines.extend(f"    - {err}" for err in outcome.errors)
    return "\n".join(lines)


def format_json(outcome: Any) -> str:
    """JSON envelope for the CLI ``--json`` output."""
    return json.dumps(
        {
            "verdict": "PASS" if outcome.ok else "FAIL",
            "ok": outcome.ok,
            "run_trade_date": outcome.run_trade_date,
            "frame_trade_date": outcome.frame_trade_date,
            "line1_rendered": outcome.line1_rendered,
            "line2_daily_rendered": outcome.line2_daily_rendered,
            "line2_intraday_rendered": outcome.line2_intraday_rendered,
            "cost_guard_spend_rmb": outcome.cost_guard_spend_rmb,
            "real_sends": 0,
            "artifact_path": outcome.artifact_path,
            "errors": list(outcome.errors),
        },
        indent=2,
        ensure_ascii=False,
    )


__all__ = [
    "ARTIFACT_SCHEMA",
    "build_artifact",
    "count_by",
    "format_json",
    "format_table",
    "write_artifact",
]
