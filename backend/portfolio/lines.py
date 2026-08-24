"""Read-side account aggregation — the per-line view (R / Z / cash).

Merges the R-line mirror ledger and the Z-line institutional-rent ledger
into one immutable view. Positions are shown at fee-inclusive cost — the
owner's real broker app is the price truth, so the mirror never attaches
market prices (research-side assumed prices stay in research code).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.portfolio.mirror_ledger import (
    DEFAULT_LEDGER as DEFAULT_MIRROR_LEDGER,
)
from backend.portfolio.mirror_ledger import MirrorBook, load_book
from backend.portfolio.z_ledger_io import (
    DEFAULT_LEDGER as DEFAULT_Z_LEDGER,
)
from backend.portfolio.z_ledger_io import load_records, summarize


@dataclass(frozen=True)
class AccountLinesView:
    """The line-split account snapshot (display/read-only)."""

    r_book: MirrorBook
    z_summary: Mapping[str, float | int]

    @property
    def r_cost_value(self) -> float:
        """Total R-line holdings at fee-inclusive cost (not market value)."""
        return round(
            sum(p.volume * p.avg_cost for p in self.r_book.positions), 2
        )


def build_account_view(
    mirror_path: Path = DEFAULT_MIRROR_LEDGER,
    z_path: Path = DEFAULT_Z_LEDGER,
) -> AccountLinesView:
    return AccountLinesView(
        r_book=load_book(mirror_path),
        z_summary=summarize(load_records(z_path)),
    )


def account_view_payload(view: AccountLinesView) -> dict[str, Any]:
    """The machine shape shared by ``account_view.py --json`` and the API."""
    return {
        "r_line": {
            "positions": [asdict(p) for p in view.r_book.positions],
            "cash": view.r_book.cash,
            "opening_declared": view.r_book.opening_declared,
            "fill_count": view.r_book.fill_count,
            "cost_value": view.r_cost_value,
        },
        "z_line": dict(view.z_summary),
    }


def render_account_lines(view: AccountLinesView) -> str:
    """Local/CLI display of the line-split account (NOT Feishu wire copy —
    every outbound Feishu message still goes through MessageRenderer)."""
    lines = ["== R 线(防御 sleeve 镜像)=="]
    if view.r_book.positions:
        for p in view.r_book.positions:
            lines.append(
                f"  {p.code}  {p.volume} 股  成本 {p.avg_cost:.4f}"
                f"  (含费市值成本 {p.volume * p.avg_cost:,.2f})"
            )
        lines.append(f"  持仓成本合计: {view.r_cost_value:,.2f} CNY")
    else:
        lines.append("  (无持仓)")
    cash_note = "" if view.r_book.opening_declared else "(本金未申报,仅为累计变动)"
    lines.append(f"  现金: {view.r_book.cash:,.2f} CNY {cash_note}".rstrip())
    lines.append(f"  已入账成交: {view.r_book.fill_count} 笔")
    z = view.z_summary
    lines.append("== Z 线(制度红利)==")
    lines.append(
        f"  实现收益累计: {float(z['realized_pnl']):,.2f} CNY"
        f"(打新卖出 {float(z['ipo_sell']):,.2f} / 转债卖出 "
        f"{float(z['cb_sell']):,.2f} / 现金收益 {float(z['cash_yield']):,.2f};"
        f"共 {int(z['records'])} 条记录)"
    )
    return "\n".join(lines)
