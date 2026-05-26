#!/usr/bin/env python
"""U-D4 — live go-live smoke: real qwen debate cost check + real Feishu round-trip.

Owner-driven. This harness costs LLM budget and sends ONE real Feishu
message, so the real-network path is gated behind ``--real`` AND the
presence of every required credential. Without ``--real`` (the default,
and what the test suite exercises) it makes **zero** network / LLM /
Feishu calls — it only prints what the owner must supply.

The render-only double-line confidence run lives in
``scripts/dry_run_double_line.py`` (U-D3). This harness adds the two
things dry-run deliberately does NOT do:

1. assert a real qwen 4-agent debate produced **0 errors**, that usage
   was **counted** on the ``llm:usage:{utc_date}`` cost-guard counter,
   and that the spend stayed **far below the ¥20/day hard cap**; and
2. a **real Feishu send** round-trip on the decision chat — a fixed
   injection-safe smoke ping rendered through ``MessageRenderer`` (the
   receive leg is the running backend's WebSocket parser; the owner
   replies ``收到 自检`` to verify it end-to-end).

The cold-start smoke is a separate concern — see
``scripts/smoke_test_cold_start.py`` (J-002).

Usage (owner-driven; real qwen + real Feishu; costs budget)::

    # Structural check only — no network, lists required credentials.
    python scripts/smoke_live_double_line.py

    # Real go-live smoke (needs DASHSCOPE + 5 Feishu creds +
    # FEISHU_DECISION_CHAT_ID + FEISHU_INTERACTIVE_ENABLED=true).
    python scripts/smoke_live_double_line.py --real

    # Real qwen debate cost check only, skip the live Feishu send.
    python scripts/smoke_live_double_line.py --real --no-feishu

Red lines: real Feishu sending is an independent owner gate; the smoke
message is a fixed literal rendered through ``MessageRenderer`` (no
user/LLM content). Cost counting is nominal — the 90-day free quota only
makes actual spend lower than the figure the guard records.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from backend.services.secrets_validator import FEISHU_CREDENTIAL_NAMES

# The ¥20/day hard cap (P1-7) — the only full-LLM circuit breaker.
_DAILY_HARD_CAP_RMB = 20.0
# A single 4-agent debate is a few thousand tokens; even at the premium
# qwen3.7-max tier it is ≪ ¥1. A delta above this means something is
# wrong (runaway tokens / mispriced model) — fail the smoke loudly.
_MAX_SMOKE_DEBATE_RMB = 1.0

# Credentials the real debate path needs. ``build_real_context`` pulls a
# real Tushare T-1 frame (TUSHARE_TOKEN) and initialises the LLMRouter,
# which eagerly resolves EVERY configured provider key from
# config/agent_models.yaml (deepseek + qwen + kimi) at init — so the
# preflight must require the whole pool, not just DASHSCOPE, or the run
# fails deep inside instead of reporting the gap up front (Codex U-D4 P2).
_DEBATE_CREDS = (
    "TUSHARE_TOKEN",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
)
# The live send leg builds FeishuClient.from_env(), which requires the
# whole 5-credential pool (FEISHU_CREDENTIAL_NAMES, incl.
# FEISHU_ALERT_CHAT_ID), plus the decision-chat id the smoke ping is sent
# to. Sourcing the pool from the canonical tuple keeps this preflight in
# sync with from_env() so --real never passes here and fails inside
# from_env() (Codex U-D4 verify P2).
_FEISHU_CREDS = (*FEISHU_CREDENTIAL_NAMES, "FEISHU_DECISION_CHAT_ID")


# ---------------------------------------------------------------------------
# Pure verdict helpers (unit-tested with fakes — zero network)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DebateSmokeVerdict:
    """Whether a real qwen debate passed the go-live cost/usage checks."""

    ok: bool
    spend_before_rmb: float | None
    spend_after_rmb: float | None
    spend_delta_rmb: float | None
    reasons: tuple[str, ...]


def evaluate_debate_smoke(
    *,
    errors: tuple[str, ...],
    spend_before_rmb: float | None,
    spend_after_rmb: float | None,
    hard_cap_rmb: float = _DAILY_HARD_CAP_RMB,
    max_smoke_rmb: float = _MAX_SMOKE_DEBATE_RMB,
) -> DebateSmokeVerdict:
    """Grade a real debate: 0 errors + usage counted + spend ≪ hard cap.

    Fail-closed: a missing spend reading (no live Redis) is treated as a
    failure for the *real* smoke because usage accounting is the whole
    point — the guard cannot prove a debate stayed under ¥20 if it never
    saw the cost.
    """
    reasons: list[str] = []
    if errors:
        reasons.append(f"debate raised {len(errors)} error(s): {errors[0]}")

    delta: float | None = None
    if spend_before_rmb is None or spend_after_rmb is None:
        reasons.append(
            "cost-guard spend unreadable (no live Redis) — cannot prove "
            "usage was counted under the ¥20 cap"
        )
    else:
        delta = round(spend_after_rmb - spend_before_rmb, 8)
        if delta <= 0:
            reasons.append(
                "usage was not counted — daily spend did not increase "
                "after the debate (llm:usage counter not written)"
            )
        if delta > max_smoke_rmb:
            reasons.append(
                f"single debate spent ¥{delta} > smoke budget "
                f"¥{max_smoke_rmb} — runaway tokens or mispriced model"
            )
        if spend_after_rmb >= hard_cap_rmb:
            reasons.append(
                f"daily spend ¥{spend_after_rmb} at/over the ¥{hard_cap_rmb} "
                "hard cap"
            )

    return DebateSmokeVerdict(
        ok=not reasons,
        spend_before_rmb=spend_before_rmb,
        spend_after_rmb=spend_after_rmb,
        spend_delta_rmb=delta,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class FeishuRoundtripVerdict:
    """Whether the live decision-chat send leg succeeded."""

    ok: bool
    sent: bool
    message_id: str | None
    reasons: tuple[str, ...]


def evaluate_feishu_roundtrip(
    *,
    send_ok: bool,
    message_id: str | None,
) -> FeishuRoundtripVerdict:
    """Grade the send leg; the receive leg is verified by the owner reply.

    The running backend's WebSocket parser handles the inbound reply —
    a script cannot block on a human reply, so the owner confirms the
    ``收到 自检`` round-trip out of band.
    """
    reasons: list[str] = []
    if not send_ok:
        reasons.append("Feishu API did not accept the smoke message")
    if not message_id:
        reasons.append("Feishu send returned no message_id")
    return FeishuRoundtripVerdict(
        ok=not reasons,
        sent=send_ok,
        message_id=message_id,
        reasons=tuple(reasons),
    )


def missing_credentials(*, include_feishu: bool) -> tuple[str, ...]:
    """Return the required env vars that are absent/empty.

    ``include_feishu`` is False for ``--no-feishu`` runs (debate cost
    check only). ``FEISHU_INTERACTIVE_ENABLED`` must be truthy for the
    live send leg — sending on the decision chat is an independent owner
    gate, never implied by mere credential presence.
    """
    required = list(_DEBATE_CREDS)
    if include_feishu:
        required.extend(_FEISHU_CREDS)
    missing = [name for name in required if not os.environ.get(name)]
    if include_feishu and os.environ.get(
        "FEISHU_INTERACTIVE_ENABLED", ""
    ).lower() not in {"1", "true", "yes"}:
        missing.append("FEISHU_INTERACTIVE_ENABLED=true")
    return tuple(missing)


# ---------------------------------------------------------------------------
# Real-network orchestration (owner-run; skip-gated)
# ---------------------------------------------------------------------------


async def _run_real_feishu_ping(*, pilot: bool) -> FeishuRoundtripVerdict:
    """Render + send ONE smoke ping to the decision chat (real network)."""
    from backend.integrations.feishu.client import FeishuClient
    from backend.integrations.feishu.renderer import MessageRenderer

    chat_id = os.environ["FEISHU_DECISION_CHAT_ID"]
    text = MessageRenderer().render_smoke_ping(
        sent_at=dt.datetime.now(tz=dt.UTC), pilot=pilot
    )
    client = FeishuClient.from_env()
    if client is None:
        # from_env returns None when FEISHU_INTERACTIVE_ENABLED is falsy.
        # missing_credentials() already gates this, so reaching here means
        # the toggle flipped mid-run — fail-closed rather than send.
        return evaluate_feishu_roundtrip(send_ok=False, message_id=None)
    result = await client.send_message(
        chat_id, text, uuid=f"smoke-{uuid.uuid4().hex}"
    )
    return evaluate_feishu_roundtrip(
        send_ok=bool(getattr(result, "ok", False)),
        message_id=getattr(result, "message_id", None),
    )


async def run_live_smoke(
    *,
    start_date: dt.date,
    send_feishu: bool,
    pilot: bool,
) -> dict[str, Any]:
    """Run the real qwen debate cost check + optional Feishu round-trip.

    Reuses ``scripts.dry_run_double_line`` for the real-data + real-qwen
    double-line walk (the 4-agent debate happens inside Line-1), wrapping
    it with a before/after cost-guard spend read so the debate's usage +
    cost can be graded against the ¥20 hard cap.
    """
    import tempfile
    from pathlib import Path

    from scripts.dry_run_double_line import (
        _read_cost_guard_spend,
        build_real_context,
        run_dry_run,
    )

    ctx = await build_real_context(start_date=start_date)
    spend_before = await _read_cost_guard_spend(ctx)
    out_path = Path(tempfile.gettempdir()) / "quantmind_smoke_artifact.json"
    outcome = await run_dry_run(ctx, start_date=start_date, out_path=out_path)
    spend_after = outcome.cost_guard_spend_rmb

    debate = evaluate_debate_smoke(
        errors=outcome.errors,
        spend_before_rmb=spend_before,
        spend_after_rmb=spend_after,
    )

    feishu: FeishuRoundtripVerdict | None = None
    if send_feishu:
        feishu = await _run_real_feishu_ping(pilot=pilot)

    overall_ok = debate.ok and (feishu is None or feishu.ok)
    return {
        "verdict": "PASS" if overall_ok else "FAIL",
        "ok": overall_ok,
        "debate": {
            "ok": debate.ok,
            "spend_before_rmb": debate.spend_before_rmb,
            "spend_after_rmb": debate.spend_after_rmb,
            "spend_delta_rmb": debate.spend_delta_rmb,
            "line1_rendered": outcome.line1_rendered,
            "reasons": list(debate.reasons),
        },
        "feishu": (
            None
            if feishu is None
            else {
                "ok": feishu.ok,
                "sent": feishu.sent,
                "message_id": feishu.message_id,
                "reasons": list(feishu.reasons),
            }
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smoke_live_double_line",
        description=(
            "Live go-live smoke — real qwen debate cost check + real "
            "Feishu round-trip. Default (no --real) makes zero network "
            "calls and only lists the credentials the owner must supply."
        ),
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Run the real-network smoke (costs LLM budget + sends ONE real "
            "Feishu message). Requires every credential to be present."
        ),
    )
    parser.add_argument(
        "--no-feishu",
        action="store_true",
        help="Run only the real qwen debate cost check; skip the live send.",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Prefix the Feishu smoke ping with the pilot banner.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON envelope to stdout."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    include_feishu = not args.no_feishu

    if not args.real:
        missing = missing_credentials(include_feishu=include_feishu)
        guidance = {
            "verdict": "SKIPPED",
            "ok": True,
            "reason": (
                "structural check only — pass --real to run the live smoke "
                "(costs LLM budget + sends a real Feishu message)"
            ),
            "missing_credentials": list(missing),
            "send_feishu": include_feishu,
        }
        if args.json:
            print(json.dumps(guidance, indent=2, ensure_ascii=False))
        else:
            print(
                "smoke_live_double_line: structural check only "
                "(no --real → zero network).",
            )
            if missing:
                print(
                    "Owner must supply before --real: " + ", ".join(missing),
                    file=sys.stderr,
                )
            else:
                print("All required credentials present — rerun with --real.")
        return 0

    missing = missing_credentials(include_feishu=include_feishu)
    if missing:
        print(
            "ERROR: --real requires every credential; missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    start_date = dt.date.today()
    result = asyncio.run(
        run_live_smoke(
            start_date=start_date,
            send_feishu=include_feishu,
            pilot=args.pilot,
        )
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"smoke verdict: {result['verdict']}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover — exercised via tests
    raise SystemExit(main())
