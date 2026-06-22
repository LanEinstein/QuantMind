"""Tests for the limit_list_d PIT reader (QGR-3 ⑦ tranche-2)."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.factor_research.limit_board_pit import read_limit_board


class _Store:
    def __init__(self, payload: bytes | None) -> None:
        self._payload = payload

    def latest(self, **_: object):  # noqa: ANN201
        if self._payload is None:
            return None
        return SimpleNamespace(raw_payload=self._payload)


def _csv(rows: list[str]) -> bytes:
    return ("ts_code,limit,limit_times,open_times\n" + "\n".join(rows) + "\n").encode()


def test_missing_snapshot_is_unavailable() -> None:
    # Pre-2020: no limit_list_d snapshot → available False, empty map.
    available, records = read_limit_board(_Store(None), "20180101")
    assert available is False
    assert records == {}


def test_parses_board_records() -> None:
    store = _Store(_csv(["603586.SH,U,3,0", "600506.SH,D,1,10", "000698.SZ,U,1,2"]))
    available, records = read_limit_board(store, "20240102")
    assert available is True
    assert records["603586.SH"] == ("U", 3.0, 0.0)
    assert records["600506.SH"] == ("D", 1.0, 10.0)
    assert records["000698.SZ"] == ("U", 1.0, 2.0)


def test_empty_board_still_available() -> None:
    # A trading day whose snapshot lists no codes is still "available" (=True),
    # so streak/broke resolve to 0 (known), not None.
    available, records = read_limit_board(_Store(_csv([])), "20240102")
    assert available is True
    assert records == {}
