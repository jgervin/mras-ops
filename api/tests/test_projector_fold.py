"""T9 — Batch fold, exercised end-to-end against synthetic (hand-inserted) events.

STATIC: no live services/emitters. Each test inserts raw ``events`` rows with a
contract-correct payload (+ any parent rows the handler needs), fences the cursor
so the fold only sees this test's events, runs the fold, and asserts:
  * the summary row appears with the right columns + resolved scope uuids;
  * the source events row was back-stamped with the scope uuids (017 indexes live);
  * REPLAY (rewind cursor, fold again) does NOT duplicate rows (idempotency);
  * a poison event lands one projector.skip audit row and the cursor still advances;
  * an unmapped event produces no summary row and no skip.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.fold import fold_batch
from src.projector.scope import ScopeResolver

CFG = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "0"})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _seed_registry(pool):
    """Idempotent: one org/location/system + camera screen_0 + display display-1."""
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
    """Set the cursor to the current max event id so the fold sees only new events."""
    fence = await pool.fetchval("SELECT COALESCE(max(id), 0) FROM events")
    await pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    return fence


async def _rewind(pool, fence):
    """Rewind the cursor to replay everything inserted after the fence."""
    await pool.execute(
        "UPDATE projector_state SET cursor=$1 WHERE id=1", fence
    )


async def _ins(pool, service, event_type, status, payload, trigger_id, offset_s=30):
    ts = datetime.now(timezone.utc) - timedelta(seconds=offset_s)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
        trigger_id, ts, service, event_type, status, json.dumps(payload),
    )


async def _run_fold(pool):
    async with pool.acquire() as conn:
        resolver = ScopeResolver(conn)
        return await fold_batch(conn, resolver, CFG)


def _cam(extra):
    return {"screen_id": "screen_0", "screen_kind": "camera", **extra}


def _disp(extra):
    return {"screen_id": "display-1", "screen_kind": "display", **extra}


# --------------------------------------------------------------------------- #
# full pipeline: every handler, one synthetic event stream
# --------------------------------------------------------------------------- #
async def test_full_pipeline_projects_every_table_with_scope(projector_pool):
    ids = await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    tid = str(uuid.uuid4())
    track = "t-full-1"

    # vision — track lifecycle
    await _ins(projector_pool, "mras-vision", "track", "opened",
               _cam({"camera_track_id": track, "started_at": "2026-07-01T12:00:00Z"}), str(uuid.uuid4()))
    await _ins(projector_pool, "mras-vision", "track", "closed",
               _cam({"camera_track_id": track, "started_at": "2026-07-01T12:00:00Z",
                     "ended_at": "2026-07-01T12:05:00Z", "observation_count": 7,
                     "track_confidence": 0.88}), str(uuid.uuid4()))
    # vision — detection (canonical observation)
    det_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": track, "confidence": 0.91,
                              "match_status": "matched_known",
                              "bounding_box": {"x": 1, "y": 2, "w": 3, "h": 4},
                              "demographic_snapshot": {"age": 30}}), tid)
    # vision — identity_match candidates (fan-out, links to detection by event id)
    await _ins(projector_pool, "mras-vision", "identity_match", "candidates",
               _cam({"detection_event_id": det_id, "candidates": [
                   {"rank": 1, "match_status": "matched", "confidence": 0.91, "qdrant_score": 0.8},
                   {"rank": 2, "match_status": "below_threshold", "confidence": 0.42},
               ]}), tid)
    # composer — decision
    dec_id = await _ins(projector_pool, "mras-composer", "decision", "made",
                        _disp({"decision_type": "identity", "decision_confidence": 0.77,
                               "decision_factors": {"why": "known face"}}), tid)
    # composer — composition (two statuses, same trigger)
    await _ins(projector_pool, "mras-composer", "composition", "queued",
               _disp({"render_mode": "remotion", "used_spoken_name": True,
                      "started_at": "2026-07-01T12:02:00Z"}), tid)
    await _ins(projector_pool, "mras-composer", "composition", "rendered",
               _disp({"render_mode": "remotion", "output_asset_ref": "s3://x",
                      "ended_at": "2026-07-01T12:03:00Z"}), tid)
    # composer — ad_run (two statuses, same trigger)
    await _ins(projector_pool, "mras-composer", "ad_run", "planned",
               _disp({"personalization_type": "identity", "used_spoken_name": True,
                      "estimated_total_viewers": 3}), tid)
    await _ins(projector_pool, "mras-composer", "ad_run", "completed",
               _disp({"personalization_type": "identity",
                      "ended_at": "2026-07-01T12:04:30Z"}), tid)
    # playback (dispatched -> started -> ended), same trigger + screen
    await _ins(projector_pool, "mras-composer", "playback", "dispatched",
               _disp({"ad_run_trigger_id": tid, "dispatched_at": "2026-07-01T12:04:00Z"}), tid)
    await _ins(projector_pool, "mras-display", "playback", "started",
               _disp({"started_at": "2026-07-01T12:04:05Z"}), tid)
    await _ins(projector_pool, "mras-display", "playback", "ended",
               _disp({"ended_at": "2026-07-01T12:04:35Z", "duration_ms": 30000}), tid)

    res = await _run_fold(projector_pool)
    assert res["skipped"] == 0

    # observation_tracks — one row, closed values won, scope stamped
    trk = await projector_pool.fetchrow(
        "SELECT * FROM observation_tracks WHERE camera_screen_id='screen_0' AND camera_track_id=$1", track
    )
    assert trk is not None
    assert trk["ended_at"] is not None
    assert trk["observation_count"] == 7
    assert trk["camera_id"] == ids["cam"]
    assert trk["system_id"] == ids["sys"]

    # subject_observations — keyed on event_id, full scope, linked to its track
    obs = await projector_pool.fetchrow("SELECT * FROM subject_observations WHERE event_id=$1", det_id)
    assert obs is not None
    assert obs["detection_type"] == "face"
    assert obs["match_status"] == "matched_known"
    assert obs["camera_id"] == ids["cam"]
    assert obs["organization_id"] == ids["org"]
    assert obs["observation_track_id"] == trk["id"]
    assert json.loads(obs["bounding_box"])["w"] == 3

    # identity_matches — 2 candidate rows for the observation
    im = await projector_pool.fetch(
        "SELECT rank, match_status FROM identity_matches WHERE subject_observation_id=$1 ORDER BY rank",
        obs["id"],
    )
    assert [(r["rank"], r["match_status"]) for r in im] == [(1, "matched"), (2, "below_threshold")]

    # personalization_decisions — keyed on event_id, display scope (no display_id col)
    dec = await projector_pool.fetchrow("SELECT * FROM personalization_decisions WHERE event_id=$1", dec_id)
    assert dec["decision_type"] == "identity"
    assert dec["system_id"] == ids["sys"]

    # composition_runs — one row, latest status won, used_spoken_name sticky-true
    comp = await projector_pool.fetchrow("SELECT * FROM composition_runs WHERE trigger_id=$1", tid)
    assert comp["status"] == "rendered"
    assert comp["render_mode"] == "remotion"
    assert comp["used_spoken_name"] is True
    assert comp["started_at"] is not None and comp["ended_at"] is not None
    assert comp["system_id"] == ids["sys"]

    # ad_runs — one row, latest status won, display scope resolved
    adr = await projector_pool.fetchrow("SELECT * FROM ad_runs WHERE trigger_id=$1", tid)
    assert adr["status"] == "completed"
    assert adr["personalization_type"] == "identity"
    assert adr["display_id"] == ids["disp"]
    assert adr["estimated_total_viewers"] == 3

    # playbacks — one row keyed (trigger_id, screen_id), latest status won, ad_run resolved
    pbs = await projector_pool.fetch("SELECT * FROM playbacks WHERE trigger_id=$1", tid)
    assert len(pbs) == 1
    pb = pbs[0]
    assert pb["status"] == "ended"
    assert pb["started_at"] is not None and pb["ended_at"] is not None
    assert pb["duration_ms"] == 30000
    assert pb["display_id"] == ids["disp"]
    assert pb["ad_run_id"] == adr["id"]

    # back-stamp — the detection events row now carries the resolved scope uuids
    evrow = await projector_pool.fetchrow(
        "SELECT camera_id, system_id, organization_id, location_id FROM events WHERE id=$1", det_id
    )
    assert evrow["camera_id"] == ids["cam"]
    assert evrow["system_id"] == ids["sys"]
    assert evrow["organization_id"] == ids["org"]
    assert evrow["location_id"] == ids["loc"]


# --------------------------------------------------------------------------- #
# replay idempotency: rewind cursor, fold again, no duplicates
# --------------------------------------------------------------------------- #
async def test_replay_from_earlier_cursor_does_not_duplicate(projector_pool):
    await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    tid = str(uuid.uuid4())
    track = "t-replay-1"

    await _ins(projector_pool, "mras-vision", "track", "opened",
               _cam({"camera_track_id": track, "started_at": "2026-07-01T12:00:00Z"}), str(uuid.uuid4()))
    det_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": track}), tid)
    await _ins(projector_pool, "mras-vision", "identity_match", "candidates",
               _cam({"detection_event_id": det_id, "candidates": [
                   {"rank": 1, "match_status": "matched", "confidence": 0.9}]}), tid)
    await _ins(projector_pool, "mras-composer", "ad_run", "planned", _disp({"personalization_type": "none"}), tid)

    await _run_fold(projector_pool)

    def counts_sql():
        return projector_pool.fetchrow(
            "SELECT "
            "(SELECT count(*) FROM observation_tracks WHERE camera_track_id=$1) AS tracks, "
            "(SELECT count(*) FROM subject_observations WHERE event_id=$2) AS obs, "
            "(SELECT count(*) FROM identity_matches im JOIN subject_observations o ON o.id=im.subject_observation_id WHERE o.event_id=$2) AS matches, "
            "(SELECT count(*) FROM ad_runs WHERE trigger_id=$3) AS runs",
            track, det_id, tid,
        )

    before = await counts_sql()
    assert (before["tracks"], before["obs"], before["matches"], before["runs"]) == (1, 1, 1, 1)

    # rewind and fold the exact same events again — upserts must converge, not duplicate
    await _rewind(projector_pool, fence)
    res2 = await _run_fold(projector_pool)
    assert res2["skipped"] == 0

    after = await counts_sql()
    assert (after["tracks"], after["obs"], after["matches"], after["runs"]) == (1, 1, 1, 1)


# --------------------------------------------------------------------------- #
# skip path: poison event -> one audit row, cursor still advances
# --------------------------------------------------------------------------- #
async def test_poison_event_is_skipped_and_cursor_advances(projector_pool):
    await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    tid = str(uuid.uuid4())

    # poison: detection with an invalid detection_type enum -> INSERT raises in the handler
    poison_id = await _ins(projector_pool, "mras-vision", "detection", "success",
                           _cam({"camera_track_id": "t-poison",
                                 "detection_type": "NOT_A_REAL_ENUM"}), tid)
    # a good event AFTER the poison — proves the batch keeps going
    good_id = await _ins(projector_pool, "mras-composer", "ad_run", "planned",
                         _disp({"personalization_type": "none"}), str(uuid.uuid4()))

    skips_before = await projector_pool.fetchval(
        "SELECT count(*) FROM audit_logs WHERE action='projector.skip' AND entity_id=$1", str(poison_id)
    )
    res = await _run_fold(projector_pool)
    assert res["skipped"] == 1
    assert res["folded"] == 1  # the good ad_run still projected

    # exactly one skip audit row for the poison event, PII-scrubbed (no payload)
    skip = await projector_pool.fetchrow(
        "SELECT actor_type, action, entity_type, entity_id, before, after "
        "FROM audit_logs WHERE action='projector.skip' AND entity_id=$1", str(poison_id)
    )
    assert skip is not None
    assert (await projector_pool.fetchval(
        "SELECT count(*) FROM audit_logs WHERE action='projector.skip' AND entity_id=$1", str(poison_id)
    )) == skips_before + 1
    after = json.loads(skip["after"])
    assert "error_class" in after and "camera_track_id" not in skip["before"]

    # poison produced NO subject_observation, but the cursor advanced past it
    assert await projector_pool.fetchval("SELECT count(*) FROM subject_observations WHERE event_id=$1", poison_id) == 0
    cursor = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
    assert cursor >= good_id  # advanced past both the poison and the good event


# --------------------------------------------------------------------------- #
# unmapped event: clean no-op skip (no row, no audit skip)
# --------------------------------------------------------------------------- #
async def test_unmapped_event_projects_nothing_and_is_not_a_skip(projector_pool):
    await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())
    gaze_id = await _ins(projector_pool, "mras-vision", "gaze", "success",
                         _cam({"track_id": "t-gaze", "attending_fraction": 0.5}), tid)

    skips_before = await projector_pool.fetchval("SELECT count(*) FROM audit_logs WHERE action='projector.skip'")
    res = await _run_fold(projector_pool)
    assert res["folded"] == 0
    assert res["skipped"] == 0
    skips_after = await projector_pool.fetchval("SELECT count(*) FROM audit_logs WHERE action='projector.skip'")
    assert skips_after == skips_before  # unmapped != error, no skip row

    # cursor still advanced past the unmapped event
    assert (await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")) >= gaze_id
