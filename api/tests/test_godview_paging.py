"""Keyset cursor round-trip for God View pagination."""
import uuid
from datetime import datetime, timezone

from src.godview.paging import encode_cursor, decode_cursor


def test_encode_decode_roundtrip():
    ts = datetime(2026, 7, 6, 18, 41, 3, tzinfo=timezone.utc)
    rid = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
    token = encode_cursor(ts, rid)
    assert isinstance(token, str)
    ts2, rid2 = decode_cursor(token)
    assert ts2 == ts
    assert rid2 == rid


def test_decode_none_is_null_pair():
    assert decode_cursor(None) == (None, None)
