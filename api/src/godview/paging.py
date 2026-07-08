"""Opaque keyset cursors for God View list endpoints.

A cursor is "<iso8601-timestamp>|<row-uuid>". Endpoints ORDER BY a
(timestamp, id) pair and resume strictly after the cursor's pair.
"""
import uuid
from datetime import datetime


def encode_cursor(ts: datetime, row_id) -> str:
    return f"{ts.isoformat()}|{row_id}"


def decode_cursor(s: str | None):
    if not s:
        return (None, None)
    iso, _, rid = s.partition("|")
    return (datetime.fromisoformat(iso), uuid.UUID(rid))
