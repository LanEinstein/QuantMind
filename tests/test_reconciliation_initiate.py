"""Tests for the on-demand OPEN-ticket builder (P0-5-amendment-2026-06-03)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.reconciliation import ReconciliationTicketStatus
from backend.services.reconciliation_initiate import (
    build_open_reconciliation_ticket,
)


def test_builds_open_ticket_with_matching_placeholder_report() -> None:
    created = datetime(2026, 6, 3, 9, 40, tzinfo=UTC)
    ticket = build_open_reconciliation_ticket(
        ticket_id="RECON-20260603-001",
        trade_date="2026-06-03",
        created_at=created,
        expected_snapshot_id="snap-abc123",
    )
    assert ticket.status is ReconciliationTicketStatus.OPEN
    assert ticket.resolved_at is None  # OPEN must not set resolved_at
    assert ticket.expected_snapshot_id == "snap-abc123"
    assert ticket.created_at == created
    # Placeholder deviation report — passed + empty, ticket_id matches the
    # ticket (the model enforces deviation_report.ticket_id == ticket.ticket_id).
    assert ticket.deviation_report.ticket_id == "RECON-20260603-001"
    assert ticket.deviation_report.overall_passed is True
    assert ticket.deviation_report.deviations == ()
    # actual_reconciliation_id is a PENDING placeholder (resolve keys by ticket_id).
    assert ticket.actual_reconciliation_id == "PENDING-RECON-20260603-001"
    assert ticket.amended_snapshot is None


def test_invalid_ticket_id_pattern_rejected() -> None:
    with pytest.raises(Exception):
        build_open_reconciliation_ticket(
            ticket_id="BADID-001",  # not RECON-\d{8}-\d{3}
            trade_date="2026-06-03",
            created_at=datetime(2026, 6, 3, 9, 40, tzinfo=UTC),
            expected_snapshot_id="snap-abc123",
        )


def test_empty_expected_snapshot_id_rejected() -> None:
    with pytest.raises(Exception):
        build_open_reconciliation_ticket(
            ticket_id="RECON-20260603-001",
            trade_date="2026-06-03",
            created_at=datetime(2026, 6, 3, 9, 40, tzinfo=UTC),
            expected_snapshot_id="",  # min_length=1
        )
