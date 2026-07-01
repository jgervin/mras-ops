"""T3 — EventEnvelope: the single contract seam over an events row.

Services write scope ONLY in payload jsonb (the typed events.* scope columns are
NULL at fold time), so the envelope reads screen_id/screen_kind and business
fields from payload. A contract change is a one-file edit here.
"""
import json

from src.projector.events import EventEnvelope


def _raw_row(**overrides):
    row = {
        "id": 42,
        "trigger_id": "11111111-1111-1111-1111-111111111111",
        "ts": "2026-07-01T00:00:00+00:00",
        "service": "mras-vision",
        "event_type": "detection",
        "status": "success",
        "payload": {
            "screen_id": "screen_0",
            "screen_kind": "camera",
            "camera_track_id": "trk-7",
            "detection_type": "face",
        },
        "asset_ref": None,
    }
    row.update(overrides)
    return row


def test_from_row_exposes_top_level_fields():
    env = EventEnvelope.from_row(_raw_row())
    assert env.id == 42
    assert env.trigger_id == "11111111-1111-1111-1111-111111111111"
    assert env.service == "mras-vision"
    assert env.event_type == "detection"
    assert env.status == "success"
    assert env.asset_ref is None


def test_match_key_is_service_event_type_status_triple():
    env = EventEnvelope.from_row(_raw_row())
    assert env.match_key == ("mras-vision", "detection", "success")


def test_scope_read_from_payload_not_typed_columns():
    # events.camera_id/system_id are NULL on the wire; scope lives in payload.
    row = _raw_row()
    row["camera_id"] = None  # typed column present but null
    env = EventEnvelope.from_row(row)
    assert env.screen_id == "screen_0"
    assert env.screen_kind == "camera"


def test_business_fields_via_payload_get_with_default():
    env = EventEnvelope.from_row(_raw_row())
    assert env.payload_get("camera_track_id") == "trk-7"
    assert env.payload_get("detection_type") == "face"
    assert env.payload_get("missing", "fallback") == "fallback"
    assert env.payload_get("missing") is None


def test_none_payload_defaults_to_empty_dict():
    env = EventEnvelope.from_row(_raw_row(payload=None))
    # payload is private; verify behavior through the public accessors
    assert env.screen_id is None
    assert env.payload_get("anything", 7) == 7
    assert env.payload_get("anything") is None


def test_payload_accepts_json_string_from_asyncpg():
    # asyncpg returns a jsonb column as a str unless a codec is registered.
    env = EventEnvelope.from_row(_raw_row(payload=json.dumps({"screen_id": "display-1", "screen_kind": "display"})))
    assert env.screen_id == "display-1"
    assert env.screen_kind == "display"
