"""MI-1 unified simulation portfolio — per-line ledgers + read-side views.

R line  — the sleeve mirror ledger (owner-reported real fills, JSONL replay);
Z line  — the institutional-rent ledger (JSONL, written by the Z CLI);
cash    — the R-line cash balance derived from declared cash + fills.

The owner's real broker account is the ONLY truth: this package records what
the owner says happened (actual fill prices), never simulates execution, and
never places orders. Research-side assumed prices stay in research code.
"""
