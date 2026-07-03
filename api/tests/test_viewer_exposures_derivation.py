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
               attention=None, mood=None, demographic=None, trigger_id=None,
               camera_track_id=None):
    # event_id left NULL (FK to events; UNIQUE treats NULLs as distinct) — these are
    # synthetic observations, not folded from real events. trigger_id is the causal
    # link the derivation uses to pick the ad_run's target observation (FIX 1).
    # camera_track_id is the durable vision tracker id the gaze join matches on.
    return await pool.fetchval(
        "INSERT INTO subject_observations (system_id, camera_id, observed_at, "
        "detection_type, subject_profile_id, match_status, attention_snapshot, mood_snapshot, "
        "demographic_snapshot, identity_confidence, trigger_id, camera_track_id) "
        "VALUES ($1,$2,$3,'face',$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11) RETURNING id",
        system_id, ids["cam"], observed_at, profile, match_status,
        json.dumps(attention) if attention is not None else None,
        json.dumps(mood) if mood is not None else None,
        json.dumps(demographic) if demographic is not None else None,
        0.91 if match_status == "matched_known" else None,
        trigger_id, camera_track_id)


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
                          mood={"mood_label": "happy", "mood_confidence": 0.7},
                          trigger_id=ids["tid"])  # FIX 1: target linked by trigger_id
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


async def test_target_is_the_pre_window_triggering_observation(projector_pool):
    """FIX 1: the causal triggering detection fires BEFORE the ad plays, so its
    observed_at is OUTSIDE [started_at, ended_at]. The target must still be selected
    by trigger_id (unconditional of the window); in-window others are bystanders; the
    target is not double-counted; out-of-window non-targets are excluded."""
    ids = await _seed(projector_pool)
    pre_window = W_START - timedelta(seconds=5)
    # causal triggering observation: BEFORE the window, linked by trigger_id
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=pre_window,
                          profile=ids["target"], match_status="matched_known",
                          attention={"attending": True, "attending_fraction": 0.8},
                          trigger_id=ids["tid"])
    # in-window bystanders (no trigger link)
    o_b1 = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
                      profile=ids["other"], match_status="matched_anonymous",
                      attention={"attending_fraction": 0.4})
    o_b2 = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=W_END,
                      profile=ids["other"], match_status="no_match")
    # out-of-window non-target: excluded
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=OUT_WINDOW,
               profile=ids["other"], match_status="no_match")

    n = await _run(projector_pool, ids["playback"])

    rows = await projector_pool.fetch(
        "SELECT subject_observation_id, role FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    by_role = {r["subject_observation_id"]: r["role"] for r in rows}
    # a target row EXISTS for the PRE-WINDOW triggering observation
    assert by_role.get(o_target) == "target"
    # in-window others are bystanders
    assert by_role.get(o_b1) == "bystander"
    assert by_role.get(o_b2) == "bystander"
    # the target is NOT double-counted as a bystander
    target_rows = [r for r in rows if r["subject_observation_id"] == o_target]
    assert len(target_rows) == 1
    assert target_rows[0]["role"] == "target"
    # out-of-window non-target excluded; total = 1 target + 2 bystanders
    assert n == 3
    assert len(rows) == 3


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


async def test_fold_derives_viewer_exposure_on_playback_interrupted(projector_pool, dedicated_conn_factory):
    """FIX 4: an INTERRUPTED playback is a closed-window terminal status. Its exposures
    must derive exactly like an ended playback — otherwise interrupted-playback exposures
    are silently dropped."""
    ids = await _seed(projector_pool)
    sfx = uuid.uuid4().hex[:8]
    cam_screen = f"cam-int-{sfx}"
    disp_screen = f"disp-int-{sfx}"
    await projector_pool.execute(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,$2,'IntCam')",
        ids["sys"], cam_screen)
    disp = await projector_pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,$2,'IntDisp') RETURNING id",
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
    await _ins(projector_pool, "mras-display", "playback", "interrupted",
               {**disp_p, "started_at": "2026-07-01T12:00:00Z",
                "ended_at": "2026-07-01T12:00:20Z", "duration_ms": 20000}, tid)

    lock_conn = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, lock_conn, CFG)
    assert await worker.acquire_lock() is True
    try:
        await worker.drain()
    finally:
        await worker.release_lock()

    ad_run = await projector_pool.fetchval("SELECT id FROM ad_runs WHERE trigger_id=$1", tid)
    exp = await projector_pool.fetch(
        "SELECT role, identity_status FROM viewer_exposures WHERE ad_run_id=$1", ad_run)
    assert len(exp) == 1
    assert exp[0]["role"] == "target"
    assert exp[0]["identity_status"] == "known"


# --------------------------------------------------------------------------- #
# 08 — gaze/success -> watched / watch_probability join
# --------------------------------------------------------------------------- #
async def _gaze(pool, *, camera_track_id, ts, attending_fraction,
                window_start=None, window_end=None, subject_profile_id=None, system_id=None):
    """Seed one mras-vision gaze/success event carrying attending_fraction and the
    durable camera_track_id the derivation joins on. system_id simulates the
    projector's back-stamp; required for cross-system isolation tests (FIX 1)."""
    payload = {"camera_track_id": camera_track_id, "screen_kind": "camera",
               "attending_fraction": attending_fraction}
    if subject_profile_id is not None:
        payload["subject_profile_id"] = str(subject_profile_id)
    if window_start is not None:
        payload["window_start"] = window_start
    if window_end is not None:
        payload["window_end"] = window_end
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload, system_id) "
        "VALUES ($1,$2,'mras-vision','gaze','success',$3::jsonb,$4) RETURNING id",
        str(uuid.uuid4()), ts, json.dumps(payload), system_id)


async def test_gaze_join_lights_up_watched_and_probability(projector_pool):
    """Decisions 3-5: the target's in-window gaze row (attending_fraction>0) sets
    watched=TRUE + attending_fraction to the MAX; the bystander's in-window gaze row
    sets watch_probability to the MAX. Out-of-window gaze is ignored. attention_snapshot
    is left NULL to prove the values come from the GAZE join, not the old fallback."""
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]  # per-test-unique track ids (shared events table)
    t_track, b_track = f"t7-{ctid}", f"t9-{ctid}"
    T5 = W_START + timedelta(seconds=5)
    T10 = W_START + timedelta(seconds=10)

    # target: pre-window detection (matched by trigger_id), NO attention_snapshot
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=5),
                          profile=ids["target"], match_status="matched_known",
                          camera_track_id=t_track, trigger_id=ids["tid"])
    # bystander: in-window detection, NO attention_snapshot
    o_by = await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
                      profile=ids["other"], match_status="matched_anonymous",
                      camera_track_id=b_track)

    await _gaze(projector_pool, camera_track_id=t_track, ts=T5, attending_fraction=0.8,
                window_start="2026-07-01T12:00:03Z", window_end="2026-07-01T12:00:06Z",
                system_id=ids["sys"])
    await _gaze(projector_pool, camera_track_id=b_track, ts=T10, attending_fraction=0.2,
                system_id=ids["sys"])
    # out-of-window gaze for the target track — MUST be ignored
    await _gaze(projector_pool, camera_track_id=t_track, ts=OUT_WINDOW, attending_fraction=0.95,
                system_id=ids["sys"])

    n = await _run(projector_pool, ids["playback"])
    assert n == 2

    rows = await projector_pool.fetch(
        "SELECT * FROM viewer_exposures WHERE ad_run_id=$1", ids["ad_run"])
    by_obs = {r["subject_observation_id"]: r for r in rows}

    tgt = by_obs[o_target]
    assert tgt["role"] == "target"
    assert tgt["watched"] is True
    assert abs(float(tgt["attending_fraction"]) - 0.8) < 1e-9
    assert tgt["gaze_duration_ms"] == 2400  # round(3s * 0.8 * 1000)

    byr = by_obs[o_by]
    assert byr["role"] == "bystander"
    assert abs(float(byr["watch_probability"]) - 0.2) < 1e-9


async def test_gaze_target_only_out_of_window_is_not_watched(projector_pool):
    """Decision 3: the target has a camera_track_id but its ONLY gaze row is out of
    the playback window -> watched=FALSE (attended-during-ad is false), not NULL."""
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]
    t_track = f"t7-{ctid}"
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=5),
                          profile=ids["target"], match_status="matched_known",
                          camera_track_id=t_track, trigger_id=ids["tid"])
    await _gaze(projector_pool, camera_track_id=t_track, ts=OUT_WINDOW, attending_fraction=0.9,
                system_id=ids["sys"])

    await _run(projector_pool, ids["playback"])
    tgt = await projector_pool.fetchrow(
        "SELECT watched, subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt["subject_observation_id"] == o_target
    assert tgt["watched"] is False


async def test_gaze_join_is_idempotent(projector_pool):
    """Decision 6: re-deriving the same playback converges — counts AND gaze-sourced
    values (watched / attending_fraction / watch_probability) are unchanged."""
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]
    t_track, b_track = f"t7-{ctid}", f"t9-{ctid}"
    await _obs(projector_pool, ids, system_id=ids["sys"],
               observed_at=W_START - timedelta(seconds=5),
               profile=ids["target"], match_status="matched_known",
               camera_track_id=t_track, trigger_id=ids["tid"])
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
               profile=ids["other"], match_status="matched_anonymous",
               camera_track_id=b_track)
    await _gaze(projector_pool, camera_track_id=t_track,
                ts=W_START + timedelta(seconds=5), attending_fraction=0.8,
                system_id=ids["sys"])
    await _gaze(projector_pool, camera_track_id=b_track,
                ts=W_START + timedelta(seconds=10), attending_fraction=0.2,
                system_id=ids["sys"])

    async def _snapshot():
        rows = await projector_pool.fetch(
            "SELECT role, watched, attending_fraction, watch_probability "
            "FROM viewer_exposures WHERE ad_run_id=$1 ORDER BY role", ids["ad_run"])
        return [(r["role"], r["watched"],
                 None if r["attending_fraction"] is None else float(r["attending_fraction"]),
                 None if r["watch_probability"] is None else float(r["watch_probability"]))
                for r in rows]

    first = await _run(projector_pool, ids["playback"])
    snap1 = await _snapshot()
    second = await _run(projector_pool, ids["playback"])
    snap2 = await _snapshot()

    assert first == second == 2
    assert snap1 == snap2
    # ORDER BY role sorts by exposure_role enum order (target, viewer, bystander, ...)
    assert snap1[0] == ("target", True, 0.8, None)
    assert snap1[1] == ("bystander", None, 0.2, 0.2)


async def test_orchestrated_target_by_profile_fallback(projector_pool):
    """Orchestrated multi-display path: the composer ORCHESTRATOR mints a NEW per-round
    trigger_id for the ad_run (ROUND-1) that does NOT equal the origin detection's
    trigger_id (DETECT-1). So the trigger_id-only primary match finds NO target
    observation. The ad_run DOES carry target_subject_profile_id, and the causal
    observation shares that subject_profile_id.

    The (subject_profile_id + system_id + observed_at <= started_at) fallback must
    select the pre-window causal detection as the target, and the gaze join must still
    light up watched=TRUE + attending_fraction from the in-window gaze row.

    RED (trigger_id-only): no target row is ever created for the orchestrated path.
    GREEN (with fallback): target row created + watched from gaze.
    """
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]
    t_track = f"t7-{ctid}"
    # causal detection: BEFORE the window, on system S, subject = the ad_run target,
    # but its trigger_id (DETECT-1) differs from the ad_run's trigger_id (ROUND-1 = ids['tid']).
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=5),
                          profile=ids["target"], match_status="matched_known",
                          camera_track_id=t_track, trigger_id=str(uuid.uuid4()))
    # in-window gaze for that track — attending_fraction=0.8
    await _gaze(projector_pool, camera_track_id=t_track, ts=W_START + timedelta(seconds=5),
                attending_fraction=0.8, system_id=ids["sys"])

    await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT role, watched, attending_fraction, subject_observation_id, subject_profile_id "
        "FROM viewer_exposures WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is not None, "orchestrated target must be attributed via profile fallback"
    assert tgt["subject_observation_id"] == o_target
    assert tgt["subject_profile_id"] == ids["target"]
    assert tgt["watched"] is True
    assert abs(float(tgt["attending_fraction"]) - 0.8) < 1e-9


async def test_orchestrated_fallback_picks_most_recent_pre_window_detection(projector_pool):
    """The fallback selects the MOST-RECENT detection before the playback (ORDER BY
    observed_at DESC LIMIT 1) — the causal one. An earlier same-subject detection on the
    same system must NOT be chosen, and a post-window one must never be picked."""
    ids = await _seed(projector_pool)
    # older same-subject detection (not causal)
    await _obs(projector_pool, ids, system_id=ids["sys"],
               observed_at=W_START - timedelta(seconds=60),
               profile=ids["target"], match_status="matched_known",
               trigger_id=str(uuid.uuid4()))
    # causal detection: most recent BEFORE the window
    o_causal = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=3),
                          profile=ids["target"], match_status="matched_known",
                          trigger_id=str(uuid.uuid4()))
    # a later same-subject detection AFTER started_at must not be chosen as target
    await _obs(projector_pool, ids, system_id=ids["sys"], observed_at=IN_WINDOW,
               profile=ids["target"], match_status="matched_known",
               trigger_id=str(uuid.uuid4()))

    await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is not None
    assert tgt["subject_observation_id"] == o_causal


async def test_orchestrated_fallback_scoped_to_system(projector_pool):
    """The profile fallback must be scoped to the playback's system_id. A same-subject
    pre-window detection on a DIFFERENT system (sys2) must NOT be attributed to this
    playback's ad_run — otherwise a person seen at another store's system would be
    falsely credited as this ad's target."""
    ids = await _seed(projector_pool)
    # same subject, pre-window, but on a DIFFERENT system (sys2)
    await _obs(projector_pool, ids, system_id=ids["sys2"],
               observed_at=W_START - timedelta(seconds=5),
               profile=ids["target"], match_status="matched_known",
               trigger_id=str(uuid.uuid4()))

    n = await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is None, "cross-system same-subject detection must NOT be attributed"
    assert n == 0


async def test_orchestrated_fallback_ignores_stale_pre_window_detection(projector_pool):
    """DBA review: the profile fallback must carry a LOWER time bound. A same-subject
    detection from HOURS ago (person long gone) must NOT be attributed — otherwise the
    derivation records a confident watched=FALSE plus stale mood/demographic snapshots
    for someone who may not have been present. Outside PROJECTOR_TARGET_LOOKBACK_S
    (default 900s) -> target_obs is None -> NO target row (honest 'no attributable
    target')."""
    ids = await _seed(projector_pool)
    # same subject, same system, but observed 3 HOURS before the playback started
    await _obs(projector_pool, ids, system_id=ids["sys"],
               observed_at=W_START - timedelta(hours=3),
               profile=ids["target"], match_status="matched_known",
               mood={"mood_label": "sad", "mood_confidence": 0.9},
               trigger_id=str(uuid.uuid4()))

    n = await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is None, "stale (beyond-lookback) detection must NOT be attributed"
    assert n == 0


async def test_orchestrated_fallback_attributes_within_lookback(projector_pool):
    """Boundary guard for the lookback bound: a pre-window detection INSIDE the default
    900s lookback (10 minutes before started_at) is still attributed as the target."""
    ids = await _seed(projector_pool)
    o_recent = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=600),
                          profile=ids["target"], match_status="matched_known",
                          trigger_id=str(uuid.uuid4()))

    n = await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is not None
    assert tgt["subject_observation_id"] == o_recent
    assert n == 1


async def test_trigger_id_primary_match_not_overridden_by_fallback(projector_pool):
    """Precedence guard: when the PRIMARY trigger_id path matches, the profile
    fallback must not run (it is gated on target_obs is None). A MORE RECENT
    same-subject pre-window detection — exactly what the fallback would pick —
    must NOT displace the exact causal trigger_id link."""
    ids = await _seed(projector_pool)
    # primary: the causal observation carrying the ad_run's trigger_id
    o_primary = await _obs(projector_pool, ids, system_id=ids["sys"],
                           observed_at=W_START - timedelta(seconds=10),
                           profile=ids["target"], match_status="matched_known",
                           trigger_id=ids["tid"])
    # decoy: more recent same-subject pre-window detection (the fallback's pick)
    await _obs(projector_pool, ids, system_id=ids["sys"],
               observed_at=W_START - timedelta(seconds=2),
               profile=ids["target"], match_status="matched_known",
               trigger_id=str(uuid.uuid4()))

    await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetch(
        "SELECT subject_observation_id FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert len(tgt) == 1
    assert tgt[0]["subject_observation_id"] == o_primary


async def test_orchestrated_fallback_is_idempotent(projector_pool):
    """Re-deriving the orchestrated (profile-fallback) case converges: same target row,
    same gaze-sourced watched/attending_fraction (COALESCE-on-conflict upsert)."""
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]
    t_track = f"t7-{ctid}"
    await _obs(projector_pool, ids, system_id=ids["sys"],
               observed_at=W_START - timedelta(seconds=5),
               profile=ids["target"], match_status="matched_known",
               camera_track_id=t_track, trigger_id=str(uuid.uuid4()))
    await _gaze(projector_pool, camera_track_id=t_track, ts=W_START + timedelta(seconds=5),
                attending_fraction=0.8, system_id=ids["sys"])

    async def _snapshot():
        rows = await projector_pool.fetch(
            "SELECT role, watched, attending_fraction FROM viewer_exposures "
            "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
        return [(r["role"], r["watched"],
                 None if r["attending_fraction"] is None else float(r["attending_fraction"]))
                for r in rows]

    first = await _run(projector_pool, ids["playback"])
    snap1 = await _snapshot()
    second = await _run(projector_pool, ids["playback"])
    snap2 = await _snapshot()

    assert first == second == 1
    assert snap1 == snap2 == [("target", True, 0.8)]


async def test_gaze_cross_system_isolation(projector_pool):
    """FIX 1: gaze join is scoped to playback.system_id (AND events.system_id = $5).

    A second system (sys2) running the same camera_track_id 't-7' in the same time
    window must NOT bleed into the target's attending_fraction. Without the system_id
    predicate, sys2's higher gaze fraction (0.9) would inflate the MAX above sys's
    true value (0.8).

    RED (before fix): MAX(0.8, 0.9) = 0.9  → assertion fails.
    GREEN (after fix): only sys rows matched → MAX = 0.8 → passes.
    """
    ids = await _seed(projector_pool)
    ctid = uuid.uuid4().hex[:6]
    t_track = f"t7-{ctid}"
    T5 = W_START + timedelta(seconds=5)

    # target observation on system A (sys)
    o_target = await _obs(projector_pool, ids, system_id=ids["sys"],
                          observed_at=W_START - timedelta(seconds=5),
                          profile=ids["target"], match_status="matched_known",
                          camera_track_id=t_track, trigger_id=ids["tid"])

    # system A gaze: attending_fraction=0.8 — the correct value for this derivation
    await _gaze(projector_pool, camera_track_id=t_track, ts=T5,
                attending_fraction=0.8, system_id=ids["sys"])
    # system B gaze: same camera_track_id, same window, higher fraction (0.9).
    # Without system_id scope, MAX(0.8, 0.9)=0.9 inflates the result (wrong).
    await _gaze(projector_pool, camera_track_id=t_track, ts=T5,
                attending_fraction=0.9, system_id=ids["sys2"])

    await _run(projector_pool, ids["playback"])

    tgt = await projector_pool.fetchrow(
        "SELECT watched, attending_fraction FROM viewer_exposures "
        "WHERE ad_run_id=$1 AND role='target'", ids["ad_run"])
    assert tgt is not None
    assert tgt["watched"] is True
    assert abs(float(tgt["attending_fraction"]) - 0.8) < 1e-9, (
        f"attending_fraction should reflect system A only (0.8), got {tgt['attending_fraction']}"
    )
