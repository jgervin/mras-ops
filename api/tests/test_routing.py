"""T8 — routing registry: (service, event_type, status) -> handler.

The registry maps the CTO-locked routing triple to exactly one projection
handler. Unknown / OPS / diagnostic keys are a clean no-op skip (route returns
None), NOT an error — the fold treats None as "nothing to project".
"""
from src.projector.events import EventEnvelope
from src.projector import handlers
from src.projector.routing import route


def _env(service, event_type, status):
    return EventEnvelope.from_row(
        {"id": 1, "trigger_id": None, "ts": None, "service": service,
         "event_type": event_type, "status": status, "payload": {}, "asset_ref": None}
    )


def test_track_events_route_to_track_handler():
    assert route(_env("mras-vision", "track", "opened")) is handlers.handle_track
    assert route(_env("mras-vision", "track", "closed")) is handlers.handle_track


def test_detection_routes_to_detection_handler():
    assert route(_env("mras-vision", "detection", "success")) is handlers.handle_detection


def test_identity_match_routes_to_identity_match_handler():
    assert route(_env("mras-vision", "identity_match", "candidates")) is handlers.handle_identity_match


def test_decision_routes_to_decision_handler():
    assert route(_env("mras-composer", "decision", "made")) is handlers.handle_decision


def test_all_composition_statuses_route_to_composition_handler():
    for s in ("queued", "rendering", "rendered", "failed"):
        assert route(_env("mras-composer", "composition", s)) is handlers.handle_composition


def test_all_ad_run_statuses_route_to_ad_run_handler():
    for s in ("planned", "dispatched", "playing", "completed", "failed"):
        assert route(_env("mras-composer", "ad_run", s)) is handlers.handle_ad_run


def test_playback_statuses_route_to_playback_handler():
    assert route(_env("mras-composer", "playback", "dispatched")) is handlers.handle_playback
    assert route(_env("mras-display", "playback", "started")) is handlers.handle_playback
    assert route(_env("mras-display", "playback", "ended")) is handlers.handle_playback


def test_unknown_and_ops_keys_route_to_none():
    assert route(_env("mras-vision", "gaze", "success")) is None
    assert route(_env("mras-vision", "detection", "error")) is None
    assert route(_env("mras-composer", "tts_attempt", "success")) is None
    assert route(_env("mras-whatever", "nope", "nope")) is None
