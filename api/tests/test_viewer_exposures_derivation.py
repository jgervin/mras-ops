"""PART B — viewer_exposures DERIVATION (projector join, not event-projection).

STATIC: no live services. Directly seed one completed playback (ad_run + scope +
[started_at, ended_at] window) and several subject_observations at the same
system, then run the derivation and assert:

  * one viewer_exposures row per IN-WINDOW, co-scope observation (018 key
    UNIQUE(ad_run_id, subject_observation_id));
  * role='target' for the observation whose subject_profile_id == the ad_run's
    target_subject_profile_id; 'bystander' for the rest;
  * scope (org/location/system/display) copied from the playback;
  * NO rows for out-of-window or other-system observations;
  * a subject observed twice in-window yields one row PER observation;
  * re-running the derivation converges (idempotent upsert on the 018 key).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.derivations import derive_viewer_exposures_for_playback
from src.projector.worker import ProjectorWorker

CFG = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "0", "PROJECTOR_BATCH_SIZE": "500"})

W_START = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
W_END = datetime(2026, 7, 1, 12, 0, 30, tzinfo=timezone.utc)
IN_WINDOW = datetime(2026, 7, 1, 12, 0, 15, tzinfo=timezone.utc)
OUT_WINDOW = datetime(2026, 7, 1, 12, 5, 0, tzinfo=timezone.utc)


async def _seed(pool):
    sfx = uuid.uuid4().hex[:8]  # screen_id is globally UNIQUE (020); keep each seed distinct
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('VeOrg','host') RETURNING id")
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('VeLoc','store') RETURNING id")
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'VeSys') RETURNING id",
        org, loc)
    sys2 = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'VeSys2') RETURNING id",
        org, loc)
    disp = await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,$2,'VeDisp') RETURNING id",
        sys, f"disp-ve-{sfx}")
    cam = await pool.fetchval(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,$2,'VeCam') RETURNING id",
        sys, f"cam-ve-{sfx}")
    target = await pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id", org)
    other = await pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'anonymous') RETURNING id", org)

    tid = str(uuid.uuid4())
    ad_run = await pool.fetchval(
        "INSERT INTO ad_runs (trigger_id, organization_id, location_id, system_id, display_id, "
        "target_subject_profile_id, status) VALUES ($1,$2,$3,$4,$5,$6,'completed') RETURNING id",
        tid, org, loc, sys, disp, target)
    playback = await pool.fetchval(
        "INSERT INTO playbacks (trigger_id, ad_run_id, organization_id, location_id, system_id, "
        "display_id, screen_id, status, started_at, ended_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,'disp-ve','ended',$7,$8) RETURNING id",
        tid, ad_run, org, loc, sys, disp, W_START, W_END)
    return {"org": org, "loc": loc, "sys": sys, "sys2": sys2, "disp": disp, "cam": cam,
            "target": target, "other": other, "tid": tid, "ad_run": ad_run, "playback": playback}


async def _obs(pool, ids, *, system_id, observed_at, profile, match_status,
               attention=None, mood=None, demographic=None):
    # event_id left NULL (FK to events; UNIQUE treats NULLs as distinct) — these are
    # synthetic observations, not folded from real events.
    return await pool.fetchval(
        "INSERT INTO subject_observations (system_id, camera_id, observed_at, "
        "detection_type, subject_profile_id, match_status, attention_snapshot, mood_snapshot, "
        "demographic_snapshot, identity_confidence) "
        "VALUES ($1,$2,$3,'face',$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9) RETURNING id",
        system_id, ids["cam"], observed_at, profile, match_status,
        json.dumps(attention) if attention is not None else None,
        json.dumps(mood) if mood is not None else None,
        json.dumps(demographic) if demographic is not None else None,
        0.91 if match_status == "matched_known" else None)


async def _run(pool, playback_id) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await derive_viewer_exposures_for_playback(conn, playback_id)


async def test_derivation_target_and_bystanders_in_window(projector_pool):
    ids = await _seed(projector_pool)
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
                          profile=ids["target"], match_status="matched_known",
                          attention={"attending": True, "attending_fraction": 0.9,
                                     "gaze_duration_ms": 5000, "visible_duration_ms": 8000},
                          mood={"mood_label": "happy", "mood_confidence": 0.7})
    o_by = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
                      profile=ids["other"], match_status="matched_anonymous",
                      attention={"attending_fraction": 0.3})
    # out-of-window: excluded
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=OUT_WINDOW,
               profile=ids["other"], match_status="no_match")
    # other-system in-window: excluded (co-scope)
    await _obs(projector_pool, ids, system_id=ids["sys2"], observed_at=IN_WINDOW,
               profile=ids["other"], match_status="no_match")

    n = await _run(projector_pool, ids["playback"])
    assert n == 2

    rows = await projector_pool.fetch(
        "SELECT * FROM viewer_exposures WHERE ad_run_id=$1 ORDER BY role", ids["ad_run"])
    assert len(rows) == 2
    by_obs = {r["subject_observation_id"]: r for r in rows}

    tgt = by_obs[o_target]
    assert tgt["role"] == "target"
    assert tgt["identity_status"] == "known"
    assert tgt["playback_id"] == ids["playback"]
    assert tgt["organization_id"] == ids["org"]
    assert tgt["location_id"] == ids["loc"]
    assert tgt["system_id"] == ids["sys"]
    assert tgt["display_id"] == ids["disp"]
    assert tgt["subject_profile_id"] == ids["target"]
    assert tgt["watched"] is True                 # target gaze from attention_snapshot
    assert tgt["gaze_duration_ms"] == 5000
    assert float(tgt["attending_fraction"]) == 0.9
    assert tgt["mood_label"] == "happy"

    byr = by_obs[o_by]
    assert byr["role"] == "bystander"
    assert byr["identity_status"] == "anonymous"
    assert byr["watched"] is None                 # bystanders carry probability, not watched
    assert float(byr["watch_probability"]) == 0.3

    # no exposure for the other-system / out-of-window observations
    total = await projector_pool.fetchval(
        "SELECT count(*) FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    assert total == 2


async def test_same_subject_twice_yields_one_row_per_observation(projector_pool):
    ids = await _seed(projector_pool)
    o1 = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
                    profile=ids["target"], match_status="matched_known")
    o2 = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=W_END,
                    profile=ids["target"], match_status="matched_known")

    n = await _run(projector_pool, ids["playback"])
    assert n == 2
    obs_ids = await projector_pool.fetch(
        "SELECT subject_observation_id FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    assert {r["subject_observation_id"] for r in obs_ids} == {o1, o2}


async def test_derivation_is_idempotent(projector_pool):
    ids = await _seed(projector_pool)
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
               profile=ids["target"], match_status="matched_known")
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
               profile=ids["other"], match_status="no_match", attention={"attending_fraction": 0.2})

    first = await _run(projector_pool, ids["playback"])
    count1 = await projector_pool.fetchval(
        "SELECT count(*) FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    second = await _run(projector_pool, ids["playback"])
    count2 = await projector_pool.fetchval(
        "SELECT count(*) FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    assert first == second == 2
    assert count1 == count2 == 2


async def test_derivation_deferred_when_window_open(projector_pool):
    ids = await _seed(projector_pool)
    # a playback with NO ended_at -> window not closed -> derive nothing (no fabrication)
    open_pb = await projector_pool.fetchval(
        "INSERT INTO playbacks (trigger_id, ad_run_id, organization_id, location_id, system_id, "
        "display_id, screen_id, status, started_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,'disp-ve','started',$7) RETURNING id",
        str(uuid.uuid4()), ids["ad_run"], ids["org"], ids["loc"], ids["sys"], ids["disp"], W_START)
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
               profile=ids["target"], match_status="matched_known")

    n = await _run(projector_pool, open_pb)
    assert n == 0
    total = await projector_pool.fetchval(
        "SELECT count(*) FROM viewer_exposures WHERE playback_id=$1", open_pb)
    assert total == 0


# --------------------------------------------------------------------------- #
# wiring: the fold triggers the derivation on playback/ended (post-projection)
# --------------------------------------------------------------------------- #
async def _ins(pool, service, event_type, status, payload, trigger_id):
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
        trigger_id, ts, service, event_type, status, json.dumps(payload))


async def test_fold_derives_viewer_exposure_on_playback_ended(projector_pool, dedicated_conn_factory):
    """End-to-end through the WORKER: a detection whose observed_at lands inside the
    playback window becomes a target viewer_exposure the moment playback/ended folds."""
    ids = await _seed(projector_pool)
    sfx = uuid.uuid4().hex[:8]
    cam_screen = f"cam-fold-{sfx}"
    disp_screen = f"disp-fold-{sfx}"
    cam = await projector_pool.fetchval(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,$2,'FoldCam') RETURNING id",
        ids["sys"], cam_screen)
    disp = await projector_pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,$2,'FoldDisp') RETURNING id",
        ids["sys"], disp_screen)

    fence = await projector_pool.fetchval("SELECT COALESCE(max(id),0) FROM events")
    await projector_pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)

    tid = str(uuid.uuid4())
    cam_p = {"screen_id": cam_screen, "screen_kind": "camera"}
    disp_p = {"screen_id": disp_screen, "screen_kind": "display"}

    await _ins(projector_pool, "mras-vision", "detection", "success",
               {**cam_p, "observed_at": "2026-07-01T12:00:15Z", "detection_type": "face",
                "match_status": "matched_known", "confidence": 0.95,
                "uuid": str(ids["target"])}, tid)
    await _ins(projector_pool, "mras-composer", "ad_run", "planned",
               {**disp_p, "personalization_type": "identity",
                "target_subject_profile_id": str(ids["target"])}, tid)
    await _ins(projector_pool, "mras-composer", "playback", "started",
               {**disp_p, "started_at": "2026-07-01T12:00:00Z"}, tid)
    await _ins(projector_pool, "mras-display", "playback", "ended",
               {**disp_p, "started_at": "2026-07-01T12:00:00Z",
                "ended_at": "2026-07-01T12:00:30Z", "duration_ms": 30000}, tid)

    lock_conn = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, lock_conn, CFG)
    assert await worker.acquire_lock() is True
    try:
        await worker.drain()
    finally:
        await worker.release_lock()

    ad_run = await projector_pool.fetchval("SELECT id FROM ad_runs WHERE trigger_id=$1", tid)
    exp = await projector_pool.fetch(
        "SELECT role, identity_status, system_id, display_id FROM viewer_exposures WHERE ad_run_id=$1",
        ad_run)
    assert len(exp) == 1
    assert exp[0]["role"] == "target"
    assert exp[0]["identity_status"] == "known"
    assert exp[0]["system_id"] == ids["sys"]
    assert exp[0]["display_id"] == disp
