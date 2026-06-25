"""Force IPv4-only egress for ``requests``/``urllib3``-based data SDKs.

P0-8-amendment-2026-06-23 (D6): §2.9 requires IPv4-only egress (the host has no
IPv6 default route). The LLM and alerter ``httpx`` clients already pin
``local_address='0.0.0.0'``, but the data SDKs (tushare / adata / akshare, which
go through ``requests`` → ``urllib3``) relied on the vendors not publishing an
AAAA record. If one ever does, Happy-Eyeballs would stall each full-market fetch
on a dead IPv6 path until the per-connection timeout — degrading the 30s data
cadence. This module makes the IPv4-only egress invariant *enforced* for that
layer, not merely assumed.
"""

from __future__ import annotations

import socket

import urllib3.util.connection as _urllib3_connection


def force_ipv4_egress() -> None:
    """Pin all ``urllib3`` (hence ``requests``) egress to IPv4.

    Overrides ``urllib3.util.connection.allowed_gai_family`` to return
    ``AF_INET`` so connection setup never resolves/attempts an AAAA address.
    Idempotent and safe on an IPv4-only host; the standard recipe for forcing
    ``requests`` onto IPv4. Call once at startup, before any data-SDK fetch.
    """
    _urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


__all__ = ["force_ipv4_egress"]
