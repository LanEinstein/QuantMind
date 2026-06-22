"""AF-001 — the DRAFT mapping's 申万 L3 codes must exist in the real catalog.

Guards against a typo'd L3 code that would silently map a theme to nothing.
Skips when the PIT snapshot store is unavailable (CI without ``data/``).
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from backend.theme_mapping.registry import (
    DEFAULT_POLICY_THEMES_PATH,
    load_policy_theme_registry,
)

_ROOT = "data/marketdata_pit"
_ENDPOINT = "index_member_all"


def _real_l3_codes() -> set[str] | None:
    try:
        from backend.marketdata_snapshot.store import SnapshotStore
    except Exception:  # pragma: no cover - defensive
        return None
    store = SnapshotStore(_ROOT)
    try:
        from scripts.factor_research.build_qgr_panel import _latest_snapshot_key

        key = _latest_snapshot_key(_ROOT, _ENDPOINT)
        snap = store.latest(vendor="tushare", endpoint=_ENDPOINT, trade_date=key)
    except Exception:
        return None
    if snap is None:
        return None
    frame = pd.read_csv(
        io.StringIO(snap.raw_payload.decode("utf-8")), dtype=str, keep_default_na=False
    )
    return {c.strip() for c in frame["l3_code"] if c.strip()}


def test_every_draft_l3_code_exists_in_catalog() -> None:
    catalog = _real_l3_codes()
    if catalog is None:
        pytest.skip("index_member_all snapshot unavailable")
    reg = load_policy_theme_registry(DEFAULT_POLICY_THEMES_PATH)
    mapped = {code for t in reg.themes for code in t.sw_l3_codes}
    missing = sorted(mapped - catalog)
    assert not missing, f"draft maps non-existent 申万 L3 codes: {missing}"
