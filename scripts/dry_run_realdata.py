"""U-D3 real-data pulls for the double-line dry-run (owner-driven path).

Extracted from :mod:`scripts.dry_run_double_line` to keep that orchestration
module under the 800-line cap (CLAUDE.md §3). These helpers reach the live
external sources the *real* dry-run needs — Tushare (T-1 EOD frame + CSI300
index closes), the configured LLM model names, the live market-data/Redis layer
— so the harness module stays focused on wiring + render-only orchestration.

Every helper is import-isolated from the harness module (no back-import → no
cycle): the harness calls them and threads the results into its
``DryRunContext``. Each degrades to a documented conservative fallback rather
than aborting (an empty index → NEUTRAL regime; a missing live layer → None →
clean offline fallback), so the dry-run never hard-fails on a missing live
seam — Line-1 (the primary signal) is fully exercised against the real frame
regardless. The Tushare frame pull itself is fail-closed (a degraded pull
raises) because a partial frame would corrupt the screen.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from backend.marketdata_snapshot import MarketDataSnapshot, SnapshotStore
from backend.utils.trading_hours import SHANGHAI

log = structlog.get_logger(component="scripts.dry_run_realdata")

CSI300 = "000300.SH"
INDEX_HISTORY_DAYS = 60

# A-share afternoon session close (Asia/Shanghai). The dry-run anchors the
# frame's provenance fetch time to this T-1 EOD moment (see t_minus_1_eod_utc).
_SESSION_CLOSE = dt.time(15, 0)


def t_minus_1_eod_utc(as_of: dt.date) -> dt.datetime:
    """The T-1 EOD logical fetch time — ``as_of`` 15:00 CST close — as UTC.

    WHY (U-D4b): the dry-run REPLAYS a *past* trading day, driving the plan's
    ``created_at`` from the replayed run-day 09:30 (``simulate_n_trading_days``).
    The live assembler default stamps ``fetch_time_utc = datetime.now(UTC)``
    (tonight's wall clock); under replay that makes snapshot_at (tonight) >=
    created_at (the past 09:30), which trips the InstructionPlan
    ``snapshot_at must be strictly before created_at`` invariant
    (``backend/models/instruction.py``) and crashes *every* plan. Anchoring the
    dry-run frame to the T-1 close (strictly before the replayed 09:30) restores
    the invariant.

    SAFE for PIT replay (R0 §3 red line A): ``fetch_time_utc`` is pure
    provenance — it is NOT part of the snapshot checksum (computed over raw
    bytes only) nor the replay feature digest, so bit-exact ``replay`` is
    unaffected. Anchoring it merely makes provenance reflect the moment the data
    actually pertains to. The injection lives ONLY in this dry-run script layer;
    production ``Line1FrameAssembler`` keeps its wall-clock default.
    """
    return dt.datetime.combine(as_of, _SESSION_CLOSE, tzinfo=SHANGHAI).astimezone(
        dt.UTC
    )


def _frame_store_root() -> str:
    """Resolve the frame-store root — a FRESH per-run temp dir by default.

    WHY a fresh store (Codex U-D4b P1): the append-only :class:`SnapshotStore`
    keys reuse on ``(vendor, endpoint, trade_date)`` + identical bytes/parents —
    ``fetch_time_utc`` is NOT a reuse key. So the persistent default
    (``data/dry_run/frames``) would return a *pre-fix* derived frame stamped
    with wall-clock time verbatim, re-triggering the snapshot_at>=created_at
    crash even with the corrected clock injected. A fresh empty store forces a
    re-fetch (Tushare is free, no ceiling) + re-stamp at the T-1 EOD anchor, so
    the fix always takes effect. Set ``QUANTMIND_DRYRUN_FRAME_ROOT`` to pin a
    persistent store for cross-run reuse — the caller then owns clearing any
    stale frames left by a pre-fix run.
    """
    root = os.environ.get("QUANTMIND_DRYRUN_FRAME_ROOT")
    return root or tempfile.mkdtemp(prefix="qm-dryrun-frames-")


async def assemble_real_frame(
    *, as_of: dt.date, signal_id: str
) -> tuple[MarketDataSnapshot, str, str]:
    """Assemble the real Tushare T-1 EOD frame; return (frame, trade_date, fp).

    Fail-closed: a degraded pull raises (never build a frame from a partial
    pull). ``fp`` is the token fingerprint (SHA256[:8], NEVER the token).
    """
    from backend.data.tushare_client import TushareClient
    from backend.orchestration.line1_frame import Line1FrameAssembler

    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN"))
    store = SnapshotStore(root=_frame_store_root())
    # Inject a T-1 EOD clock so the replayed dry-run's snapshot_at stays
    # strictly before the replayed run-day created_at (U-D4b). ``as_of`` is the
    # frame's T-1 trade date; ``fetch_time_utc`` is the single source both lines
    # read (Line-1 context-provider snapshot_at + Line-2 daily
    # ``Line2DailyProvider(snapshot_at=frame.fetch_time_utc)``), so one anchor
    # fixes both lines.
    assembler = Line1FrameAssembler(
        client=client, store=store, now_utc=lambda: t_minus_1_eod_utc(as_of)
    )
    result = await assembler.assemble(as_of_date=as_of, signal_id=signal_id)
    snap = result.frame_snapshot
    return snap, snap.trade_date, client.token_fingerprint


async def pull_index_closes(*, end: dt.date) -> tuple[float, ...]:
    """Pull recent CSI300 closes (prereq 4) oldest→newest; () on failure.

    An empty tuple → ``classify_regime`` returns NEUTRAL (no bear-regime ADD
    ban) — the conservative fallback the spec mandates when the pull fails.
    """
    from backend.data.tushare_client import TushareClient

    try:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN"))
        start = end - dt.timedelta(days=INDEX_HISTORY_DAYS * 2)
        df = await client.index_daily(
            CSI300, start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as exc:  # noqa: BLE001 — () is the conservative NEUTRAL
        log.warning("dry_run_index_pull_failed", error=str(exc))
        return ()
    if df is None or df.empty or "close" not in df.columns:
        return ()
    # Tushare index_daily returns newest→oldest by trade_date; sort ascending.
    ordered = df.sort_values("trade_date") if "trade_date" in df.columns else df
    return tuple(float(c) for c in ordered["close"].tolist() if c == c)  # drop NaN


def resolve_llm_models() -> tuple[str, ...]:
    """Best-effort read of the configured debate models (artifact metadata)."""
    try:
        import yaml

        cfg = yaml.safe_load(Path("config/agent_models.yaml").read_text("utf-8"))
        models = {
            v.get("model")
            for v in (cfg.get("agents", {}) or {}).values()
            if isinstance(v, dict) and v.get("model")
        }
        return tuple(sorted(m for m in models if m))
    except Exception:  # noqa: BLE001 — metadata only
        return ()


async def build_data_layer() -> tuple[Any, Any, Any]:
    """Build the live market_data / market_meta / DataQualityProvider if able.

    A fresh offline dry-run cannot reach the live quote vendors / Mongo; the
    documented fallbacks then engage (market_data=None → empty spots so the
    intraday runner finds no fresh quotes; dq_provider=None → clean fallback in
    ``build_line2_code_contexts``). The real owner-driven run with the live
    stack available supplies all three (prereq 1 fully engaged).
    """
    market_data: Any = None
    market_meta: Any = None
    dq_provider: Any = None
    try:
        from backend.data.config import load_data_sources_config
        from backend.data.market_data import MarketDataService

        market_data = MarketDataService(load_data_sources_config())
    except Exception as exc:  # noqa: BLE001 — documented offline fallback
        log.info("dry_run_market_data_unavailable", error=str(exc))
    return market_data, market_meta, dq_provider


def build_redis_or_none() -> Any:
    """Best-effort live Redis for the real run's cost_guard + LLM router.

    Used by the LLM router (init + cost tracking) and Line-1's debate budget
    reservation, and for the post-run spend read. Matches ``backend.main``'s
    client (``REDIS_URL`` + ``decode_responses=True``) so the cost_guard
    string-valued counters parse identically. Returns ``None`` offline — the
    real owner-driven run has the live 127.0.0.1 Redis; a None client
    fail-closes the budget-reserving debate rather than running untracked.
    """
    try:
        import redis.asyncio as aioredis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return aioredis.from_url(url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 — spend metadata is best-effort
        log.info("dry_run_redis_unavailable", error=str(exc))
        return None


__all__ = [
    "CSI300",
    "INDEX_HISTORY_DAYS",
    "assemble_real_frame",
    "build_data_layer",
    "build_redis_or_none",
    "pull_index_closes",
    "resolve_llm_models",
    "t_minus_1_eod_utc",
]
