"""Review fixes for the God View projector projection layer (STATIC, synthetic).

Five independent fixes, each with its own red->green test. No live services:
every test hand-inserts raw ``events`` rows (+ parent rows), fences the cursor,
folds, and asserts on the projected/back-stamped state.

  FIX 1 — defaulted-bind clobber: a later lifecycle event that omits render_mode /
          personalization_type must not overwrite the earlier real value.
  FIX 2 — back-stamp events.subject_profile_id (detection) and events.ad_run_id
          (ad_run / playback), not just the 5 device-scope columns.
  FIX 3 — handle_identity_match prefers the explicit detection_event_id link over
          the trigger_id LIMIT 1 fallback.
  FIX 4 — the settle window is a STOP boundary, not a filter: a held-back low-id
          event is never skipped by the cursor jumping past it.
  FIX 5 — a missing REQUIRED PARENT ROW audits as 'projector.resolve_miss', not
          'projector.skip'.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.fold import fold_batch
from src.projector.scope import ScopeResolver

CFG0 = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "0"})
CFG_SETTLE = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "60000"})


# --------------------------------------------------------------------------- #
# helpers (self-contained; mirror test_projector_fold.py)
# --------------------------------------------------------------------------- #
async def _seed_registry(pool):
    existing = await pool.fetchrow(
        "SELECT c.id AS cam, c.system_id AS sys, s.organization_id AS org, s.location_id AS loc "
        "FROM cameras c JOIN systems s ON s.id = c.system_id WHERE c.screen_id='screen_0'"
    )
    if existing:
        disp = await pool.fetchval("SELECT id FROM displays WHERE screen_id='display-1'")
        return {"org": existing["org"], "loc": existing["loc"], "sys": existing["sys"],
                "cam": existing["cam"], "disp": disp}
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('Acme','host') RETURNING id"
    )
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('Store 1','store') RETURNING id"
    )
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'Sys 1') RETURNING id",
        org, loc,
    )
    cam = await pool.fetchval(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,'screen_0','Cam') RETURNING id", sys
    )
    disp = await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,'display-1','Disp') RETURNING id", sys
    )
    return {"org": org, "loc": loc, "sys": sys, "cam": cam, "disp": disp}


async def _fence(pool) -> int:
    fence = await pool.fetchval("SELECT COALESCE(max(id), 0) FROM events")
    await pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    return fence


async def _ins(pool, service, event_type, status, payload, trigger_id, offset_s=30):
    ts = datetime.now(timezone.utc) - timedelta(seconds=offset_s)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
        trigger_id, ts, service, event_type, status, json.dumps(payload),
    )


async def _run_fold(pool, cfg=CFG0):
    async with pool.acquire() as conn:
        resolver = ScopeResolver(conn)
        return await fold_batch(conn, resolver, cfg)


def _cam(extra):
    return {"screen_id": "screen_0", "screen_kind": "camera", **extra}


def _disp(extra):
    return {"screen_id": "display-1", "screen_kind": "display", **extra}


# --------------------------------------------------------------------------- #
# FIX 1 — defaulted-bind clobber
# --------------------------------------------------------------------------- #
async def test_composition_render_mode_not_clobbered_by_later_omitting_event(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    # queued carries the real render_mode; rendered OMITS it (lifecycle event).
    await _ins(projector_pool, "mras-composer", "composition", "queued",
               _disp({"render_mode": "remotion", "started_at": "2026-07-01T12:00:00Z"}), tid)
    await _ins(projector_pool, "mras-composer", "composition", "rendered",
               _disp({"ended_at": "2026-07-01T12:01:00Z"}), tid)  # no render_mode

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    comp = await projector_pool.fetchrow("SELECT * FROM composition_runs WHERE trigger_id=$1", tid)
    assert comp["status"] == "rendered"
    assert comp["render_mode"] == "remotion"  # NOT clobbered back to 'prebuilt'


async def test_ad_run_personalization_type_not_clobbered_by_later_omitting_event(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    await _ins(projector_pool, "mras-composer", "ad_run", "planned",
               _disp({"personalization_type": "identity"}), tid)
    await _ins(projector_pool, "mras-composer", "ad_run", "completed",
               _disp({"ended_at": "2026-07-01T12:04:30Z"}), tid)  # no personalization_type

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    adr = await projector_pool.fetchrow("SELECT * FROM ad_runs WHERE trigger_id=$1", tid)
    assert adr["status"] == "completed"
    assert adr["personalization_type"] == "identity"  # NOT clobbered back to 'none'


# --------------------------------------------------------------------------- #
# FIX 2 — back-stamp the derivable event-scope columns
# --------------------------------------------------------------------------- #
async def test_detection_backstamps_events_subject_profile_id(projector_pool):
    ids = await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())
    profile = await projector_pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id",
        ids["org"],
    )

    det_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": "t-fix2", "match_status": "matched_known",
                              "subject_profile_id": str(profile)}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    # the observation carries the profile, and the events row is now back-stamped
    obs = await projector_pool.fetchrow("SELECT subject_profile_id FROM subject_observations WHERE event_id=$1", det_id)
    assert obs["subject_profile_id"] == profile
    ev = await projector_pool.fetchrow("SELECT subject_profile_id FROM events WHERE id=$1", det_id)
    assert ev["subject_profile_id"] == profile  # events_subject index now live


async def test_ad_run_and_playback_backstamp_events_ad_run_id(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    ar_id = await _ins(projector_pool, "mras-composer", "ad_run", "planned",
                       _disp({"personalization_type": "none"}), tid)
    pb_id = await _ins(projector_pool, "mras-composer", "playback", "dispatched",
                       _disp({"ad_run_trigger_id": tid, "dispatched_at": "2026-07-01T12:04:00Z"}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    adr = await projector_pool.fetchrow("SELECT id FROM ad_runs WHERE trigger_id=$1", tid)
    assert adr is not None

    # the ad_run event back-stamps events.ad_run_id with the row it projected
    ev_ar = await projector_pool.fetchrow("SELECT ad_run_id FROM events WHERE id=$1", ar_id)
    assert ev_ar["ad_run_id"] == adr["id"]  # events_ad_run_idx now indexes this row

    # the playback event back-stamps events.ad_run_id with the resolved ad_run
    ev_pb = await projector_pool.fetchrow("SELECT ad_run_id FROM events WHERE id=$1", pb_id)
    assert ev_pb["ad_run_id"] == adr["id"]


# --------------------------------------------------------------------------- #
# FIX 3 — prefer the explicit detection link
# --------------------------------------------------------------------------- #
async def test_identity_match_prefers_detection_event_id_link(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())  # ONE trigger shared by two observations

    det1 = await _ins(projector_pool, "mras-vision", "detection", "success",
                      _cam({"camera_track_id": "t-a"}), tid)
    det2 = await _ins(projector_pool, "mras-vision", "detection", "success",
                      _cam({"camera_track_id": "t-b"}), tid)
    # identity_match explicitly links to the SECOND detection
    await _ins(projector_pool, "mras-vision", "identity_match", "candidates",
               _cam({"detection_event_id": det2, "candidates": [
                   {"rank": 1, "match_status": "matched", "confidence": 0.9}]}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    obs2 = await projector_pool.fetchval("SELECT id FROM subject_observations WHERE event_id=$1", det2)
    obs1 = await projector_pool.fetchval("SELECT id FROM subject_observations WHERE event_id=$1", det1)
    # candidates attached to obs2 (the explicit link), NOT obs1 (the earliest by id)
    n2 = await projector_pool.fetchval(
        "SELECT count(*) FROM identity_matches WHERE subject_observation_id=$1", obs2
    )
    n1 = await projector_pool.fetchval(
        "SELECT count(*) FROM identity_matches WHERE subject_observation_id=$1", obs1
    )
    assert (n2, n1) == (1, 0)


async def test_identity_match_falls_back_to_trigger_id_without_detection_link(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    det = await _ins(projector_pool, "mras-vision", "detection", "success",
                     _cam({"camera_track_id": "t-fb"}), tid)
    # NO detection_event_id -> trigger_id fallback
    await _ins(projector_pool, "mras-vision", "identity_match", "candidates",
               _cam({"candidates": [{"rank": 1, "match_status": "matched", "confidence": 0.9}]}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    obs = await projector_pool.fetchval("SELECT id FROM subject_observations WHERE event_id=$1", det)
    n = await projector_pool.fetchval(
        "SELECT count(*) FROM identity_matches WHERE subject_observation_id=$1", obs
    )
    assert n == 1


# --------------------------------------------------------------------------- #
# FIX 4 — settle window is a STOP boundary, not a filter
# --------------------------------------------------------------------------- #
async def test_settle_window_stops_at_first_unsettled_and_never_skips_lower_id(projector_pool):
    await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())

    # e1 is UNSETTLED (ts ~ now), e2 is SETTLED (ts old), and e1.id < e2.id.
    e1 = await _ins(projector_pool, "mras-composer", "ad_run", "planned",
                    _disp({"personalization_type": "none"}), t1, offset_s=5)
    e2 = await _ins(projector_pool, "mras-composer", "ad_run", "planned",
                    _disp({"personalization_type": "none"}), t2, offset_s=120)
    assert e1 < e2

    # fold with a 60s settle window: e1 is held back -> STOP; e2 must NOT be reached.
    res = await _run_fold(projector_pool, CFG_SETTLE)
    assert res["folded"] == 0
    assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs WHERE trigger_id=$1", t1) == 0
    assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs WHERE trigger_id=$1", t2) == 0
    # cursor did NOT advance past the held-back e1 (stays at the fence)
    cur = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
    assert cur == fence
    assert cur < e1

    # once e1 settles (no settle window), a later fold processes BOTH e1 then e2 — neither skipped.
    res2 = await _run_fold(projector_pool, CFG0)
    assert res2["folded"] == 2
    assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs WHERE trigger_id=$1", t1) == 1
    assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs WHERE trigger_id=$1", t2) == 1
    cur2 = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
    assert cur2 == e2


# --------------------------------------------------------------------------- #
# FIX 5 — resolve-miss audit action
# --------------------------------------------------------------------------- #
async def test_missing_parent_observation_audits_resolve_miss_not_skip(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())  # no detection exists for this trigger

    im_id = await _ins(projector_pool, "mras-vision", "identity_match", "candidates",
                       _cam({"candidates": [{"rank": 1, "match_status": "matched", "confidence": 0.9}]}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 1

    # exactly one resolve_miss audit row, and NO plain skip row, for this event
    miss = await projector_pool.fetchval(
        "SELECT count(*) FROM audit_logs WHERE action='projector.resolve_miss' AND entity_id=$1", str(im_id)
    )
    assert miss == 1
    skip = await projector_pool.fetchval(
        "SELECT count(*) FROM audit_logs WHERE action='projector.skip' AND entity_id=$1", str(im_id)
    )
    assert skip == 0

    # cursor still advances past the resolve-miss event
    cur = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
    assert cur >= im_id


# --------------------------------------------------------------------------- #
# FIX 6 — real vision detection/success payload contract
#
# Vision emits: subject_profile_id, confidence, is_new_visitor, scene_context,
# camera_track_id, attention_snapshot, match_status, screen_id, screen_kind.
# match_status is 'matched_known' when the person was recognized, 'no_match'
# otherwise (cross-repo contract, landing in mras-vision PR #22). The handler
# keeps its 'no_match' default for ABSENT match_status (older/other emitters).
# Vision does NOT emit: uuid, observed_at, detection_type.
#
# Before fix: handler reads uuid (NULL) and observed_at (NULL -> NOT NULL violation)
#   -> event is routed to projector.skip.
# After fix:  handler reads subject_profile_id from payload, observed_at from
#   env.ts, detection_type defaults to 'face' -> INSERT succeeds.
# --------------------------------------------------------------------------- #
async def test_detection_real_vision_payload_inserts_subject_observation(projector_pool):
    """Prove RED (current handler) then GREEN (after 3-field fix) against real vision payload."""
    ids = await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    profile_id = await projector_pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id",
        ids["org"],
    )

    # Real vision detection/success payload — exactly the keys vision emits.
    # Deliberately omits uuid / observed_at / detection_type.
    det_id = await _ins(
        projector_pool, "mras-vision", "detection", "success",
        _cam({
            "camera_track_id": "t-7",
            "subject_profile_id": str(profile_id),
            "confidence": 0.88,
            "is_new_visitor": False,
            "scene_context": {"ambient_light": "bright"},
            "attention_snapshot": {"gaze_direction": "forward"},
            "match_status": "matched_known",  # vision contract (mras-vision PR #22)
        }),
        tid,
    )

    # Capture the event's own ts so we can verify observed_at is sourced from it.
    event_ts = await projector_pool.fetchval("SELECT ts FROM events WHERE id=$1", det_id)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0, f"handler raised (likely observed_at NOT NULL or bad enum): {res}"

    obs = await projector_pool.fetchrow(
        "SELECT * FROM subject_observations WHERE event_id=$1", det_id
    )
    assert obs is not None, "subject_observations row was not created"
    assert obs["subject_profile_id"] == profile_id
    assert obs["camera_track_id"] == "t-7"
    assert obs["detection_type"] == "face"
    assert obs["match_status"] == "matched_known"
    assert obs["system_id"] == ids["sys"]
    assert obs["observed_at"] == event_ts


async def test_detection_absent_match_status_defaults_to_no_match(projector_pool):
    """Guard: an emitter that omits match_status entirely (older/other emitters)
    still projects with the 'no_match' floor — the absent-key default must survive
    the matched_known contract change."""
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    det_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": "t-abs"}), tid)  # NO match_status key

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    obs = await projector_pool.fetchrow(
        "SELECT match_status FROM subject_observations WHERE event_id=$1", det_id
    )
    assert obs["match_status"] == "no_match"


# --------------------------------------------------------------------------- #
# FIX 7 — explicit JSON null must not bypass the detection defaults
#
# payload_get(key, default) only defaults on an ABSENT key; a payload carrying
# an explicit JSON null ("detection_type": null) returns None, which reaches the
# INSERT as SQL NULL — violating detection_type's NOT NULL and losing the
# match_status 'no_match' floor. The handler must treat explicit null like
# absent: detection_type -> 'face', match_status -> 'no_match'.
# --------------------------------------------------------------------------- #
async def test_detection_explicit_json_null_gets_defaults(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())

    det_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": "t-nul",
                              "detection_type": None,      # explicit JSON null
                              "match_status": None}), tid)  # explicit JSON null

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0, f"explicit-null payload must not skip: {res}"

    obs = await projector_pool.fetchrow(
        "SELECT detection_type, match_status FROM subject_observations WHERE event_id=$1", det_id
    )
    assert obs is not None
    assert obs["detection_type"] == "face"
    assert obs["match_status"] == "no_match"
