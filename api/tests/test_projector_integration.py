"""T12 — Offline end-to-end integration test (the capstone).

STATIC: no live services. A hand-built SYNTHETIC event stream for ONE trigger,
spanning every service in the pipeline, is inserted into the throwaway DB; the
WORKER's fold path (worker.drain) is run to completion; then every summary table,
every back-stamped events scope column, the cursor, and full-run idempotency are
asserted.

Stream (single trigger_id, one registered camera + display + system):
  track/opened -> track/closed            (mras-vision)   -> observation_tracks
  detection/success                       (mras-vision)   -> subject_observations
  identity_match/candidates               (mras-vision)   -> identity_matches (x2)
  decision/made                           (mras-composer) -> personalization_decisions
  composition/queued -> composition/rendered (mras-composer) -> composition_runs
  ad_run/planned -> ad_run/dispatched     (mras-composer) -> ad_runs
  playback/dispatched -> started -> ended (mras-composer/display) -> playbacks

Back-stamp assertions (fold._backstamp):
  * detection event  -> events.subject_profile_id (from handler return)
  * ad_run events    -> events.ad_run_id (from handler return)
  * playback events  -> events.ad_run_id (resolved via ad_run_trigger_id)
  * scoped events    -> events.camera_id / display_id / system_id / org / loc
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.worker import ProjectorWorker

CFG = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "0", "PROJECTOR_BATCH_SIZE": "500"})


# --------------------------------------------------------------------------- #
# registry + synthetic-event helpers
# --------------------------------------------------------------------------- #
async def _seed_registry(pool):
    """One org/location/system + camera 'cam-int' + display 'disp-int' + a known
    subject_profile whose id the detection event carries (drives the
    subject_profile_id back-stamp).

    Idempotent — the throwaway DB is module-scoped and shared across tests."""
    existing = await pool.fetchrow(
        "SELECT c.id AS cam, c.system_id AS sys, s.organization_id AS org, s.location_id AS loc "
        "FROM cameras c JOIN systems s ON s.id=c.system_id WHERE c.screen_id='cam-int'"
    )
    if existing:
        disp = await pool.fetchval("SELECT id FROM displays WHERE screen_id='disp-int'")
        profile = await pool.fetchval(
            "SELECT id FROM subject_profiles WHERE organization_id=$1 ORDER BY created_at LIMIT 1",
            existing["org"],
        )
        return {"org": existing["org"], "loc": existing["loc"], "sys": existing["sys"],
                "cam": existing["cam"], "disp": disp, "profile": profile}
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('IntOrg','host') RETURNING id"
    )
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('IntLoc','store') RETURNING id"
    )
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'IntSys') RETURNING id",
        org, loc,
    )
    cam = await pool.fetchval(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,'cam-int','IntCam') RETURNING id", sys
    )
    disp = await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,'disp-int','IntDisp') RETURNING id", sys
    )
    profile = await pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id", org
    )
    return {"org": org, "loc": loc, "sys": sys, "cam": cam, "disp": disp, "profile": profile}


async def _fence(pool) -> int:
    fence = await pool.fetchval("SELECT COALESCE(max(id), 0) FROM events")
    await pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    return fence


async def _ins(pool, service, event_type, status, payload, trigger_id):
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
        trigger_id, ts, service, event_type, status, json.dumps(payload),
    )


def _cam(extra):
    return {"screen_id": "cam-int", "screen_kind": "camera", **extra}


def _disp(extra):
    return {"screen_id": "disp-int", "screen_kind": "display", **extra}


async def _insert_stream(pool, ids):
    """Insert the full single-trigger event stream. Returns useful event ids."""
    tid = str(uuid.uuid4())
    track = "trk-int-1"

    await _ins(pool, "mras-vision", "track", "opened",
               _cam({"camera_track_id": track, "started_at": "2026-07-01T12:00:00Z"}), str(uuid.uuid4()))
    await _ins(pool, "mras-vision", "track", "closed",
               _cam({"camera_track_id": track, "started_at": "2026-07-01T12:00:00Z",
                     "ended_at": "2026-07-01T12:05:00Z", "observation_count": 9,
                     "track_confidence": 0.9}), str(uuid.uuid4()))

    det_id = await _ins(pool, "mras-vision", "detection", "success",
                        _cam({"camera_track_id": track, "observed_at": "2026-07-01T12:01:00Z",
                              "detection_type": "face", "confidence": 0.93,
                              "match_status": "matched_known", "uuid": str(ids["profile"]),
                              "bounding_box": {"x": 1, "y": 2, "w": 3, "h": 4}}), tid)

    await _ins(pool, "mras-vision", "identity_match", "candidates",
               _cam({"detection_event_id": det_id, "candidates": [
                   {"rank": 1, "match_status": "matched", "confidence": 0.93,
                    "candidate_subject_profile_id": str(ids["profile"])},
                   {"rank": 2, "match_status": "below_threshold", "confidence": 0.4}]}), tid)

    dec_id = await _ins(pool, "mras-composer", "decision", "made",
                        _disp({"decision_type": "identity", "decision_confidence": 0.8,
                               "decision_factors": {"why": "known face"}}), tid)

    await _ins(pool, "mras-composer", "composition", "queued",
               _disp({"render_mode": "remotion", "used_spoken_name": True,
                      "started_at": "2026-07-01T12:02:00Z"}), tid)
    await _ins(pool, "mras-composer", "composition", "rendered",
               _disp({"render_mode": "remotion", "ended_at": "2026-07-01T12:03:00Z"}), tid)

    adr_planned = await _ins(pool, "mras-composer", "ad_run", "planned",
                             _disp({"personalization_type": "identity", "used_spoken_name": True,
                                    "estimated_total_viewers": 4}), tid)
    adr_dispatched = await _ins(pool, "mras-composer", "ad_run", "dispatched",
                                _disp({"personalization_type": "identity"}), tid)

    pb_dispatched = await _ins(pool, "mras-composer", "playback", "dispatched",
                               _disp({"ad_run_trigger_id": tid, "dispatched_at": "2026-07-01T12:04:00Z"}), tid)
    pb_started = await _ins(pool, "mras-display", "playback", "started",
                            _disp({"ad_run_trigger_id": tid, "started_at": "2026-07-01T12:04:05Z"}), tid)
    pb_ended = await _ins(pool, "mras-display", "playback", "ended",
                          _disp({"ad_run_trigger_id": tid, "ended_at": "2026-07-01T12:04:35Z",
                                 "duration_ms": 30000}), tid)

    return {
        "tid": tid, "track": track, "det_id": det_id, "dec_id": dec_id,
        "adr_planned": adr_planned, "adr_dispatched": adr_dispatched,
        "pb_dispatched": pb_dispatched, "pb_started": pb_started, "pb_ended": pb_ended,
    }


async def _drain_with_worker(pool, dedicated_conn_factory):
    """Run the worker's fold path to completion (acquire lock -> drain -> release)."""
    lock_conn = await dedicated_conn_factory()
    worker = ProjectorWorker(pool, lock_conn, CFG)
    assert await worker.acquire_lock() is True
    try:
        await worker.drain()
    finally:
        await worker.release_lock()


async def _summary_counts(pool, ev, ids):
    return await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM observation_tracks WHERE camera_track_id=$1)               AS tracks,
          (SELECT count(*) FROM subject_observations WHERE event_id=$2)                     AS obs,
          (SELECT count(*) FROM identity_matches im JOIN subject_observations o
                 ON o.id=im.subject_observation_id WHERE o.event_id=$2)                     AS matches,
          (SELECT count(*) FROM personalization_decisions WHERE event_id=$3)               AS decisions,
          (SELECT count(*) FROM composition_runs WHERE trigger_id=$4)                       AS comps,
          (SELECT count(*) FROM ad_runs WHERE trigger_id=$4)                                AS runs,
          (SELECT count(*) FROM playbacks WHERE trigger_id=$4)                              AS playbacks
        """,
        ev["track"], ev["det_id"], ev["dec_id"], ev["tid"],
    )


# --------------------------------------------------------------------------- #
# capstone: full stream -> every table + scope + back-stamp + cursor
# --------------------------------------------------------------------------- #
async def test_end_to_end_stream_projects_every_table(projector_pool, dedicated_conn_factory):
    ids = await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    ev = await _insert_stream(projector_pool, ids)
    last_id = await projector_pool.fetchval("SELECT max(id) FROM events")

    await _drain_with_worker(projector_pool, dedicated_conn_factory)

    # --- every summary table populated exactly once (per key) ---
    counts = await _summary_counts(projector_pool, ev, ids)
    assert counts["tracks"] == 1
    assert counts["obs"] == 1
    assert counts["matches"] == 2
    assert counts["decisions"] == 1
    assert counts["comps"] == 1
    assert counts["runs"] == 1
    assert counts["playbacks"] == 1

    # --- observation_tracks: closed-state won, scope resolved ---
    trk = await projector_pool.fetchrow(
        "SELECT * FROM observation_tracks WHERE camera_track_id=$1", ev["track"])
    assert trk["ended_at"] is not None
    assert trk["observation_count"] == 9
    assert trk["camera_id"] == ids["cam"] and trk["system_id"] == ids["sys"]

    # --- subject_observations: full scope + link to its track + profile ---
    obs = await projector_pool.fetchrow(
        "SELECT * FROM subject_observations WHERE event_id=$1", ev["det_id"])
    assert obs["detection_type"] == "face"
    assert obs["match_status"] == "matched_known"
    assert obs["camera_id"] == ids["cam"]
    assert obs["organization_id"] == ids["org"]
    assert obs["location_id"] == ids["loc"]
    assert obs["observation_track_id"] == trk["id"]
    assert obs["subject_profile_id"] == ids["profile"]

    # --- identity_matches: two ranks for the observation ---
    im = await projector_pool.fetch(
        "SELECT rank, match_status FROM identity_matches WHERE subject_observation_id=$1 ORDER BY rank",
        obs["id"])
    assert [(r["rank"], r["match_status"]) for r in im] == [(1, "matched"), (2, "below_threshold")]

    # --- personalization_decisions: display scope resolved ---
    dec = await projector_pool.fetchrow(
        "SELECT * FROM personalization_decisions WHERE event_id=$1", ev["dec_id"])
    assert dec["decision_type"] == "identity"
    assert dec["system_id"] == ids["sys"]

    # --- composition_runs: latest status won, sticky used_spoken_name ---
    comp = await projector_pool.fetchrow(
        "SELECT * FROM composition_runs WHERE trigger_id=$1", ev["tid"])
    assert comp["status"] == "rendered"
    assert comp["used_spoken_name"] is True
    assert comp["started_at"] is not None and comp["ended_at"] is not None

    # --- ad_runs: latest status won, display scope resolved ---
    adr = await projector_pool.fetchrow("SELECT * FROM ad_runs WHERE trigger_id=$1", ev["tid"])
    assert adr["status"] == "dispatched"
    assert adr["display_id"] == ids["disp"]
    assert adr["estimated_total_viewers"] == 4

    # --- playbacks: one row, ended-state won, ad_run + display resolved ---
    pb = await projector_pool.fetchrow("SELECT * FROM playbacks WHERE trigger_id=$1", ev["tid"])
    assert pb["status"] == "ended"
    assert pb["duration_ms"] == 30000
    assert pb["display_id"] == ids["disp"]
    assert pb["ad_run_id"] == adr["id"]

    # --- back-stamp: detection event carries scope + subject_profile_id ---
    det_ev = await projector_pool.fetchrow(
        "SELECT camera_id, system_id, organization_id, location_id, subject_profile_id "
        "FROM events WHERE id=$1", ev["det_id"])
    assert det_ev["camera_id"] == ids["cam"]
    assert det_ev["organization_id"] == ids["org"]
    assert det_ev["location_id"] == ids["loc"]
    assert det_ev["subject_profile_id"] == ids["profile"]

    # --- back-stamp: ad_run + playback events carry ad_run_id + display scope ---
    for eid in (ev["adr_planned"], ev["adr_dispatched"], ev["pb_dispatched"], ev["pb_started"], ev["pb_ended"]):
        row = await projector_pool.fetchrow(
            "SELECT display_id, ad_run_id FROM events WHERE id=$1", eid)
        assert row["display_id"] == ids["disp"]
        assert row["ad_run_id"] == adr["id"]

    # --- cursor advanced to the last event ---
    cursor = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
    assert cursor == last_id
    assert fence < cursor


# --------------------------------------------------------------------------- #
# idempotency: a SECOND full run over the same stream duplicates nothing
# --------------------------------------------------------------------------- #
async def test_second_full_run_is_idempotent(projector_pool, dedicated_conn_factory):
    ids = await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    ev = await _insert_stream(projector_pool, ids)

    await _drain_with_worker(projector_pool, dedicated_conn_factory)
    before = await _summary_counts(projector_pool, ev, ids)
    assert (before["tracks"], before["obs"], before["matches"], before["decisions"],
            before["comps"], before["runs"], before["playbacks"]) == (1, 1, 2, 1, 1, 1, 1)

    # rewind the cursor and fold the identical stream again — upserts must converge.
    await projector_pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    await _drain_with_worker(projector_pool, dedicated_conn_factory)

    after = await _summary_counts(projector_pool, ev, ids)
    assert (after["tracks"], after["obs"], after["matches"], after["decisions"],
            after["comps"], after["runs"], after["playbacks"]) == \
           (before["tracks"], before["obs"], before["matches"], before["decisions"],
            before["comps"], before["runs"], before["playbacks"])

    # no phantom skips on the clean replay
    skips = await projector_pool.fetchval(
        "SELECT count(*) FROM audit_logs WHERE action IN ('projector.skip','projector.resolve_miss') "
        "AND entity_id=$1", str(ev["det_id"]))
    assert skips == 0
