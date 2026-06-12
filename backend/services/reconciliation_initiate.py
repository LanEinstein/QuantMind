"""Build an OPEN reconciliation ticket for on-demand initiation.

P0-5-amendment-2026-06-03: the reconciliation INITIATE / ticket-creation path
was never wired into production (``initiate_reconciliation`` had no caller; no
``ReconciliationTicket`` was ever constructed). This module provides the single
pure constructor for the OPEN ticket the ``scripts/reconcile_now.py`` ops tool
persists; the owner's Feishu replies then flow through the already-wired
``ReconciliationOrchestrator.handle_reply`` → ``decide_ticket`` →
``ReconciliationApplier.reset_to_snapshot``.

The ticket is created OPEN with a PLACEHOLDER (passed, empty) deviation report:
the real deviation is recomputed by ``_handle_mismatch`` (via ``detect_deviations``
against ``expected_snapshot_id``) when the owner reports their actual holdings —
it is never stored back onto the ticket, so a placeholder here is correct and
fail-closed (it never masquerades as a real deviation verdict).

This module never mutates the broker mirror and never imports
``backend.{llm,agents,mirofish}`` — it only constructs a frozen model.
"""

from __future__ import annotations

from datetime import datetime

from backend.models.reconciliation import (
    DeviationReport,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)


def build_open_reconciliation_ticket(
    *,
    ticket_id: str,
    trade_date: str,
    created_at: datetime,
    expected_snapshot_id: str,
    deviation_report: DeviationReport | None = None,
) -> ReconciliationTicket:
    """Construct an OPEN :class:`ReconciliationTicket` for on-demand initiation.

    Args:
        ticket_id: matches ``^RECON-\\d{8}-\\d{3}$`` (validated by the model).
        trade_date: ``YYYY-MM-DD``.
        created_at: ticket creation timestamp (tz-aware).
        expected_snapshot_id: the ``snapshot_id`` of a persisted
            ``BrokerSnapshot`` (resolvable by ``MongoSnapshotLookup`` from the
            ``broker_snapshots`` collection) representing the CURRENT mirror —
            so the owner's reported holdings show as a deviation on reply.
        deviation_report: a real, already-computed deviation report
            (AA-001 sim auto-reconciliation embeds its three-way
            integrity comparison). ``None`` keeps the owner-initiated
            placeholder semantics documented above. Its ``ticket_id``
            must match (the model enforces this).

    Returns:
        A frozen OPEN ticket whose ``ticket_id`` matches its deviation
        report (the model enforces this). ``actual_reconciliation_id``
        is a ``PENDING-`` placeholder — the resolve path keys the
        user-reported snapshot by ``ticket_id``, not by this field.
    """
    return ReconciliationTicket(
        ticket_id=ticket_id,
        trade_date=trade_date,
        created_at=created_at,
        deviation_report=(
            deviation_report
            if deviation_report is not None
            else DeviationReport(
                ticket_id=ticket_id,
                overall_passed=True,
                deviations=(),
            )
        ),
        expected_snapshot_id=expected_snapshot_id,
        actual_reconciliation_id=f"PENDING-{ticket_id}",
        status=ReconciliationTicketStatus.OPEN,
    )


__all__ = ["build_open_reconciliation_ticket"]
