"""T3 — EventEnvelope: typed wrapper over an ``events`` row and payload adapter.

This is the ONE contract seam. Handlers never touch a raw row; they read the
envelope. Reconcile any event-contract change here and nowhere else.

Scope rule (DBA/architect freeze): services write scope only inside the payload
jsonb — the typed ``events`` scope columns (camera_id/system_id/…) are NULL at
fold time. So screen_id/screen_kind and every business field come from payload.
"""
import json
from dataclasses import dataclass
from typing import Any


def _get(row, key, default=None):
    """Read a column from an asyncpg Record or a plain dict, tolerating absence."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


@dataclass(frozen=True)
class EventEnvelope:
    id: int
    trigger_id: Any
    ts: Any
    service: str
    event_type: str
    status: str
    _payload: dict
    asset_ref: str | None = None

    @classmethod
    def from_row(cls, row) -> "EventEnvelope":
        payload = _get(row, "payload")
        if payload is None:
            payload = {}
        elif isinstance(payload, (str, bytes, bytearray)):
            payload = json.loads(payload)
        return cls(
            id=_get(row, "id"),
            trigger_id=_get(row, "trigger_id"),
            ts=_get(row, "ts"),
            service=_get(row, "service"),
            event_type=_get(row, "event_type"),
            status=_get(row, "status"),
            _payload=payload,
            asset_ref=_get(row, "asset_ref"),
        )

    @property
    def match_key(self) -> tuple:
        """Routing identity — the CTO-locked (service, event_type, status) triple."""
        return (self.service, self.event_type, self.status)

    def payload_get(self, key, default=None):
        """Sole public accessor for payload fields (payload field is private)."""
        return self._payload.get(key, default)

    # --- scope inputs (payload-only; typed columns are NULL on the wire) ---
    @property
    def screen_id(self):
        return self._payload.get("screen_id")

    @property
    def screen_kind(self):
        return self._payload.get("screen_kind")
