"""T8 — routing registry: (service, event_type, status) -> projection handler.

The routing identity is the CTO-locked triple (contract 01 §0). Exactly one
handler per summary table; a key not in the registry (OPS/error/diagnostic
events, or anything unmapped) resolves to ``None`` — the fold treats that as a
clean no-op skip, distinct from a handler that raised (which is audited).

playback started/ended are relayed by the composer terminus for the display, so
both ``mras-display`` and ``mras-composer`` are registered for the playback
statuses to tolerate whichever service string the relay stamps.
"""
from src.projector import handlers

_REGISTRY = {
    # observation lane (vision)
    ("mras-vision", "track", "opened"): handlers.handle_track,
    ("mras-vision", "track", "closed"): handlers.handle_track,
    ("mras-vision", "detection", "success"): handlers.handle_detection,
    ("mras-vision", "identity_match", "candidates"): handlers.handle_identity_match,
    # run lane (composer)
    ("mras-composer", "decision", "made"): handlers.handle_decision,
    ("mras-composer", "composition", "queued"): handlers.handle_composition,
    ("mras-composer", "composition", "rendering"): handlers.handle_composition,
    ("mras-composer", "composition", "rendered"): handlers.handle_composition,
    ("mras-composer", "composition", "failed"): handlers.handle_composition,
    ("mras-composer", "ad_run", "planned"): handlers.handle_ad_run,
    ("mras-composer", "ad_run", "dispatched"): handlers.handle_ad_run,
    ("mras-composer", "ad_run", "playing"): handlers.handle_ad_run,
    ("mras-composer", "ad_run", "completed"): handlers.handle_ad_run,
    ("mras-composer", "ad_run", "failed"): handlers.handle_ad_run,
    # playback lane (composer dispatch + display relay via composer terminus)
    ("mras-composer", "playback", "dispatched"): handlers.handle_playback,
    ("mras-composer", "playback", "started"): handlers.handle_playback,
    ("mras-composer", "playback", "ended"): handlers.handle_playback,
    ("mras-display", "playback", "dispatched"): handlers.handle_playback,
    ("mras-display", "playback", "started"): handlers.handle_playback,
    ("mras-display", "playback", "ended"): handlers.handle_playback,
}


def route(env):
    """Return the handler for this event's match_key, or None for an unmapped key."""
    return _REGISTRY.get(env.match_key)
