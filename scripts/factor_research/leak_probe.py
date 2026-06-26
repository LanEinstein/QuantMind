"""future-NaN poison leak gate (main-force-intent P0 infrastructure).

The single most dangerous research bug is **look-ahead**: a forward bar quietly
feeding a feature so the backtest "knows" the future. PIT discipline (features
use bars ``<= d``, labels use bars ``> d``) is *asserted by construction* in the
panel builders, but construction can be wrong — a slice off-by-one, a `<=` that
should be `<`, a vendor frame whose row for day d is only complete after the
close. This module *empirically* falsifies leakage instead of trusting the code.

The probe (macro program §4 / low-base design §6.1): poison every snapshot whose
knowledge timestamp is **after** an as-of ``cutoff`` (set its endpoint invisible),
rebuild the panel, and assert that every **feature** column for the rebalance
dates ``<= cutoff`` is **byte-for-byte identical** to the un-poisoned build. If a
feature changed, some code read a bar it should not have — a leak. Label columns
(``fwd_ret_*``) are *allowed* to change (they SHOULD vanish to NaN once the
forward bars are poisoned away — that is the point, not a leak).

Scope: this gate poisons the **per-trade-date market endpoints** (``daily`` /
``stk_limit`` / … — keyed by ``trade_date``, which IS their knowledge date: a
bar for day d is knowable only after d's close). Catalog (as-of) and period-keyed
fundamental endpoints carry their own PIT key (``asof`` / ``ann_date``) and are
guarded by *their* builders (``IndustryPIT`` / ``FundamentalsPIT``), so they pass
through untouched — poisoning them by ``trade_date`` would be a category error.

Pure + deterministic: duck-typed over any ``latest()``-bearing store (no
``backend`` import, no IO, no RNG, no wall-clock). Dates are canonical zero-padded
``YYYYMMDD`` strings, so lexical ``>`` is chronological ``>``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

# The per-trade-date market endpoints whose `trade_date` IS the knowledge date.
# Catalog (asof) + period (*_vip) endpoints are deliberately excluded (see module
# docstring) — their PIT key is asof / ann_date, guarded by their own builders.
MARKET_TRADE_DATE_ENDPOINTS: tuple[str, ...] = (
    "daily",
    "daily_basic",
    "adj_factor",
    "fund_daily",
    "stk_limit",
    "limit_list_d",
    "suspend_d",
    "cyq_perf",
    "stk_factor_pro",
    "moneyflow",  # never on the signal path, but poison-correct if ever read
)


@runtime_checkable
class StoreLike(Protocol):
    """Minimal snapshot-store surface the probe needs (duck-typed)."""

    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> object | None: ...


class FutureLeakError(AssertionError):
    """Raised by :func:`assert_no_future_leak` when a forward bar fed a feature."""


class PoisonedStore:
    """A ``StoreLike`` view that hides future market snapshots past ``cutoff``.

    ``latest`` / ``versions`` (the two per-``trade_date`` read surfaces) return
    ``None`` / ``[]`` (the bar is unknowable) when ``endpoint`` is poisoned AND
    ``trade_date > cutoff``; every other call delegates to the wrapped store via
    ``__getattr__``. Read-only: never mutates the wrapped store.

    Coverage caveat (review #9): the cutoff is enforced only on the two
    ``trade_date``-keyed surfaces. A builder that read market bars through some
    *other* surface (a bulk ``get`` by id, a range query) would bypass the poison,
    so the gate's promise holds **for builders that read bars via latest/versions**
    — which the round-1/QGR/crowding panel builders all do. A new builder that
    invents another market-read path must be probed explicitly.
    """

    def __init__(
        self,
        store: StoreLike,
        *,
        cutoff: str,
        poisoned_endpoints: Collection[str] = MARKET_TRADE_DATE_ENDPOINTS,
    ) -> None:
        self._store = store
        self._cutoff = str(cutoff)
        self._poisoned = frozenset(poisoned_endpoints)

    def _hidden(self, endpoint: str, trade_date: str) -> bool:
        return endpoint in self._poisoned and str(trade_date) > self._cutoff

    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> object | None:
        if self._hidden(endpoint, trade_date):
            return None
        return self._store.latest(
            vendor=vendor, endpoint=endpoint, trade_date=trade_date
        )

    def versions(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> list[object]:
        if self._hidden(endpoint, trade_date):
            return []
        return self._store.versions(  # type: ignore[attr-defined,no-any-return]
            vendor=vendor, endpoint=endpoint, trade_date=trade_date
        )

    def __getattr__(self, name: str) -> object:
        # Reached only when normal lookup misses (so latest/versions above always
        # win); delegate any other store method the builder happens to call.
        return getattr(self._store, name)


@dataclass(frozen=True)
class LeakReport:
    """Outcome of a poison-probe (immutable)."""

    leaked: bool
    cutoff: str
    n_rows_checked: int
    feature_cols: tuple[str, ...]
    mismatched_cols: tuple[str, ...]
    vanished_keys: int
    detail: str


def _scalar_equal(x: object, y: object) -> bool:
    """Exact equality with ``NaN``/``None`` treated as a single 'missing' value."""
    x_na = x is None or (isinstance(x, float) and math.isnan(x))
    y_na = y is None or (isinstance(y, float) and math.isnan(y))
    if x_na or y_na:
        return x_na and y_na
    return bool(x == y)


def _column_equal(a: pd.Series, b: pd.Series) -> bool:
    """Element-wise exact equality of two aligned series (NaN==NaN, no tolerance)."""
    av = a.to_numpy()
    bv = b.to_numpy()
    if len(av) != len(bv):
        return False
    return all(_scalar_equal(x, y) for x, y in zip(av, bv, strict=True))


def check_future_leak(
    build_fn: Callable[[StoreLike], pd.DataFrame],
    store: StoreLike,
    *,
    cutoff: str,
    feature_cols: Sequence[str],
    poisoned_endpoints: Collection[str] = MARKET_TRADE_DATE_ENDPOINTS,
    key_cols: Sequence[str] = ("date", "code"),
    date_col: str = "date",
    min_rows_checked: int = 1,
) -> LeakReport:
    """Build the panel twice (full vs future-poisoned) and compare features.

    ``build_fn(store)`` must return a tidy panel with ``date_col`` + ``key_cols`` +
    ``feature_cols``. Returns a :class:`LeakReport`: ``leaked`` is True if any
    feature value differs for a rebalance date ``<= cutoff``, a pre-cutoff row
    present in the full build vanished under poison (its existence depended on a
    future bar), OR fewer than ``min_rows_checked`` rows were actually compared
    (review #2 — an empty / all-post-cutoff build must FAIL the gate, not pass it
    vacuously). Label columns are NOT compared — they are expected to change.

    Raises ``ValueError`` if a requested ``feature_col`` is absent from the build
    (review #3 — a typo / renamed factor must not silently skip its leak check).

    Coverage (review #1): one cutoff stress-tests only the rebalance dates whose
    forward bars cross it (``cutoff − horizon < d <= cutoff``). Use
    :func:`assert_no_future_leak_sweep` to stress every interior date.
    """
    cutoff = str(cutoff)
    cols = tuple(feature_cols)
    keys = list(key_cols)
    full = build_fn(store)
    missing = [c for c in cols if c not in full.columns]
    if missing:
        raise ValueError(
            f"leak probe: requested feature_cols absent from the build: {missing} "
            "(typo or renamed factor — cannot silently skip its leak check)"
        )
    poisoned = build_fn(
        PoisonedStore(store, cutoff=cutoff, poisoned_endpoints=poisoned_endpoints)
    )

    def _safe(frame: pd.DataFrame) -> pd.DataFrame:
        sub = frame[frame[date_col].astype(str) <= cutoff].copy()
        return sub.set_index(keys).sort_index()

    full_s, pois_s = _safe(full), _safe(poisoned)
    common = full_s.index.intersection(pois_s.index)
    vanished = full_s.index.difference(pois_s.index)  # rows that needed the future

    fa, pa = full_s.loc[common], pois_s.loc[common]
    mismatched = tuple(c for c in cols if not _column_equal(fa[c], pa[c]))
    too_few = len(common) < min_rows_checked
    leaked = bool(mismatched) or len(vanished) > 0 or too_few
    if leaked:
        bits = []
        if mismatched:
            bits.append(f"features changed under future-poison: {list(mismatched)}")
        if len(vanished):
            bits.append(
                f"{len(vanished)} pre-cutoff row(s) vanished (existence used a "
                "future bar)"
            )
        if too_few:
            bits.append(
                f"only {len(common)} rows compared (< min {min_rows_checked}) — "
                "vacuous pass refused"
            )
        detail = "FUTURE LEAK at cutoff " + cutoff + " — " + "; ".join(bits)
    else:
        detail = (
            f"no future leak: {len(common)} pre-cutoff rows × {len(cols)} feature "
            f"cols byte-identical under poison (cutoff {cutoff})"
        )
    return LeakReport(
        leaked=leaked,
        cutoff=cutoff,
        n_rows_checked=len(common),
        feature_cols=cols,
        mismatched_cols=mismatched,
        vanished_keys=len(vanished),
        detail=detail,
    )


def assert_no_future_leak(
    build_fn: Callable[[StoreLike], pd.DataFrame],
    store: StoreLike,
    *,
    cutoff: str,
    feature_cols: Sequence[str],
    poisoned_endpoints: Collection[str] = MARKET_TRADE_DATE_ENDPOINTS,
    key_cols: Sequence[str] = ("date", "code"),
    date_col: str = "date",
    min_rows_checked: int = 1,
) -> LeakReport:
    """:func:`check_future_leak`, raising :class:`FutureLeakError` on a leak."""
    report = check_future_leak(
        build_fn,
        store,
        cutoff=cutoff,
        feature_cols=feature_cols,
        poisoned_endpoints=poisoned_endpoints,
        key_cols=key_cols,
        date_col=date_col,
        min_rows_checked=min_rows_checked,
    )
    if report.leaked:
        raise FutureLeakError(report.detail)
    return report


def assert_no_future_leak_sweep(
    build_fn: Callable[[StoreLike], pd.DataFrame],
    store: StoreLike,
    *,
    cutoffs: Sequence[str],
    feature_cols: Sequence[str],
    poisoned_endpoints: Collection[str] = MARKET_TRADE_DATE_ENDPOINTS,
    key_cols: Sequence[str] = ("date", "code"),
    date_col: str = "date",
    min_rows_checked: int = 1,
) -> tuple[LeakReport, ...]:
    """Run :func:`assert_no_future_leak` at EACH cutoff (review #1).

    A single cutoff only stress-tests the rebalance dates whose forward bars cross
    it; a sweep of cutoffs spread across the window stress-tests each cutoff's
    interior boundary, so a one-step-ahead leak at an interior date is caught when
    a cutoff sits just above it. Each cutoff must compare ``>= min_rows_checked``
    rows (else FAIL, not a vacuous pass). Raises on the first leaking cutoff;
    returns every cutoff's clean report on success.
    """
    if not cutoffs:
        raise ValueError("leak sweep needs at least one cutoff")
    reports: list[LeakReport] = []
    for c in cutoffs:
        reports.append(
            assert_no_future_leak(
                build_fn,
                store,
                cutoff=c,
                feature_cols=feature_cols,
                poisoned_endpoints=poisoned_endpoints,
                key_cols=key_cols,
                date_col=date_col,
                min_rows_checked=min_rows_checked,
            )
        )
    return tuple(reports)


__all__ = [
    "MARKET_TRADE_DATE_ENDPOINTS",
    "FutureLeakError",
    "LeakReport",
    "PoisonedStore",
    "StoreLike",
    "assert_no_future_leak",
    "assert_no_future_leak_sweep",
    "check_future_leak",
]
