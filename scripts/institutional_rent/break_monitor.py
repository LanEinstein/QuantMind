"""Rolling break-issue monitor — the policy-dependence kill rule (MZ-1).

The IPO rent exists only while the IPO throttle holds; its known death is
the 2021–2022 break-issue wave. Protocol §3: over the last ``WINDOW``
listed names (stocks and CBs independently), ``broken >= KILL_THRESHOLD``
stops that category's reminders (a one-time stop notice is pushed on the
transition; recovery is an owner decision, never automatic).

Break definition: first listed-day close < issue price (CBs: < 100 par).
First-day closes come from tiny per-code ``daily``/``cb_daily`` queries
and are cached locally so steady-state runs make ~1 calendar query per
category. Functions are pure over an injected ``query`` callable and
return new cache dicts (no in-place mutation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.institutional_rent.calendars import QueryFn, normalize_date

WINDOW = 20
KILL_THRESHOLD = 4
CB_PAR = 100.0
# ~110 stock IPOs and ~50 CB issues per year: 540 calendar days always
# covers the WINDOW most recent listings for both categories.
_LISTING_LOOKBACK_DAYS = 540


@dataclass(frozen=True)
class BreakStats:
    evaluated: int
    broken: int

    @property
    def killed(self) -> bool:
        return self.broken >= KILL_THRESHOLD


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {"stocks": {}, "cbs": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"stocks": dict(data.get("stocks", {})), "cbs": dict(data.get("cbs", {}))}


def save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _first_close(
    query: QueryFn, endpoint: str, ts_code: str, list_date: str
) -> float | None:
    frame = query(endpoint, ts_code=ts_code, start_date=list_date, end_date=list_date)
    if frame.empty:
        return None
    close = frame.to_dict("records")[0].get("close")
    return float(close) if isinstance(close, int | float) else None


def _lookback_start(today: str) -> str:
    return (
        datetime.strptime(today, "%Y%m%d") - timedelta(days=_LISTING_LOOKBACK_DAYS)
    ).strftime("%Y%m%d")


def stock_break_stats(
    query: QueryFn, today: str, cache: dict[str, dict[str, Any]]
) -> tuple[BreakStats, dict[str, dict[str, Any]]]:
    """(stats, new_cache) over the last WINDOW listed A-share IPOs (ex-BSE)."""
    frame = query("new_share", start_date=_lookback_start(today), end_date=today)
    listed: list[dict[str, Any]] = []
    if not frame.empty:
        for row in frame.to_dict("records"):
            ts_code = str(row.get("ts_code", ""))
            issue_date = normalize_date(row.get("issue_date") or "")
            price = row.get("price")
            if (
                not ts_code
                or ts_code.endswith(".BJ")
                or not issue_date
                or issue_date == "None"
                or issue_date > today
                or not isinstance(price, int | float)
                or float(price) <= 0
            ):
                continue
            listed.append(
                {"ts_code": ts_code, "issue_date": issue_date, "price": float(price)}
            )
    listed.sort(key=lambda r: (r["issue_date"], r["ts_code"]), reverse=True)
    new_stocks = dict(cache.get("stocks", {}))
    evaluated = broken = 0
    # Scan until WINDOW listings are actually evaluable: a listing-morning
    # row with no closing bar yet must be SUBSTITUTED by the next older one,
    # never shrink the window (codex P1 — a broken issue squeezed out of a
    # short window could miss the kill threshold).
    for row in listed:
        if evaluated >= WINDOW:
            break
        entry = new_stocks.get(row["ts_code"])
        if entry is None:
            first_close = _first_close(
                query, "daily", row["ts_code"], row["issue_date"]
            )
            if first_close is None:
                continue  # no bar yet — retried on the next run, never cached
            entry = {
                "issue_date": row["issue_date"],
                "price": row["price"],
                "first_close": first_close,
            }
            new_stocks[row["ts_code"]] = entry
        evaluated += 1
        if float(entry["first_close"]) < float(entry["price"]):
            broken += 1
    return BreakStats(evaluated=evaluated, broken=broken), {
        "stocks": new_stocks,
        "cbs": dict(cache.get("cbs", {})),
    }


def cb_break_stats(
    query: QueryFn, today: str, cache: dict[str, dict[str, Any]]
) -> tuple[BreakStats, dict[str, dict[str, Any]]]:
    """(stats, new_cache) over the last WINDOW listed convertible bonds."""
    frame = query("cb_basic")
    listed: list[dict[str, Any]] = []
    if not frame.empty:
        for row in frame.to_dict("records"):
            ts_code = str(row.get("ts_code", ""))
            list_date = normalize_date(row.get("list_date") or "")
            if not ts_code or not list_date or list_date == "None" or list_date > today:
                continue
            listed.append({"ts_code": ts_code, "list_date": list_date})
    listed.sort(key=lambda r: (r["list_date"], r["ts_code"]), reverse=True)
    new_cbs = dict(cache.get("cbs", {}))
    evaluated = broken = 0
    for row in listed:  # same substitution rule as the stock loop
        if evaluated >= WINDOW:
            break
        entry = new_cbs.get(row["ts_code"])
        if entry is None:
            first_close = _first_close(
                query, "cb_daily", row["ts_code"], row["list_date"]
            )
            if first_close is None:
                continue
            entry = {"list_date": row["list_date"], "first_close": first_close}
            new_cbs[row["ts_code"]] = entry
        evaluated += 1
        if float(entry["first_close"]) < CB_PAR:
            broken += 1
    return BreakStats(evaluated=evaluated, broken=broken), {
        "stocks": dict(cache.get("stocks", {})),
        "cbs": new_cbs,
    }


_KILL_STATE_KEYS = ("stock", "cb", "stock_notified", "cb_notified")


def load_kill_state(path: Path) -> dict[str, bool]:
    """The persisted kill state (killed latch + notice-delivered flags)."""
    if not path.exists():
        return dict.fromkeys(_KILL_STATE_KEYS, False)
    stored = json.loads(path.read_text(encoding="utf-8"))
    return {key: bool(stored.get(key)) for key in _KILL_STATE_KEYS}


def _write_kill_state(path: Path, state: dict[str, bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def latch_kill_state(
    path: Path, *, stock_killed: bool, cb_killed: bool, persist: bool = True
) -> dict[str, bool]:
    """OR today's kill verdicts into the stored latch; return the new state.

    The killed latch and the notice-delivered flag are SEPARATE (codex P1):
    latching must never imply the one-time stop notice reached the owner —
    the caller marks delivery via :func:`mark_notice_delivered` only after a
    successful send, so a failed push retries the notice on the next run.
    Recovery (killed → not) is deliberately NOT automatic: a killed category
    stays killed until the owner clears the state file (protocol §3 — the
    throttle regime is asymmetric, recovery is a human decision).
    """
    prev = load_kill_state(path)
    latched = {
        **prev,
        "stock": prev["stock"] or stock_killed,
        "cb": prev["cb"] or cb_killed,
    }
    if persist and latched != prev:
        _write_kill_state(path, latched)
    return latched


def mark_notice_delivered(path: Path, categories: tuple[str, ...]) -> None:
    """Record that the stop notice for ``categories`` reached the owner."""
    state = load_kill_state(path)
    updated = {
        **state,
        **{f"{category}_notified": True for category in categories},
    }
    if updated != state:
        _write_kill_state(path, updated)
