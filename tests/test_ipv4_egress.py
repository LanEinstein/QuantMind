"""force_ipv4_egress pins urllib3/requests to IPv4 (P0-8-amendment-2026-06-23 D6)."""

from __future__ import annotations

import socket

import urllib3.util.connection as _u3c

from backend.data.ipv4_egress import force_ipv4_egress


def test_force_ipv4_egress_pins_af_inet() -> None:
    original = _u3c.allowed_gai_family
    try:
        force_ipv4_egress()
        assert _u3c.allowed_gai_family() == socket.AF_INET
    finally:
        # Restore so this global monkeypatch does not leak into other tests.
        _u3c.allowed_gai_family = original
