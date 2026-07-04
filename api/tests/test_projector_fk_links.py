"""PART A — FK-link resolution by shared trigger_id.

STATIC: no live services. A synthetic decision -> composition -> ad_run ->
playback stream for ONE trigger_id is inserted into the throwaway DB; the worker
folds it; then the sibling FKs the projector must resolve are asserted NON-NULL:

  * composition_runs.personalization_decision_id  <- personalization_decisions (trigger)
  * ad_runs.composition_run_id                    <- composition_runs (trigger)
  * ad_runs.personalization_decision_id           <- personalization_decisions (trigger)
  * playbacks.ad_run_id                            <- ad_runs (trigger)
  * playbacks.media_asset_id                       <- media_assets (media_asset_ref -> storage_url)

The projector folds strictly in ascending events.id order, so each parent summary
row already exists when the dependent event folds. Replay stays idempotent.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.worker import ProjectorWorker

CFG = ProjectorConfig.from_env({"PROJECTOR_SETTLE_MS": "0", "PROJECTOR_BATCH_SIZE": "500"})


async def _seed_registry(pool):
    existing = await pool.fetchrow(
        "SELECT d.id AS disp, d.system_id AS sys, s.organization_id AS org, s.location_id AS loc "
        "FROM displays d JOIN systems s ON s.id=d.system_id WHERE d.screen_id='disp-fk'"
    )
    if existing:
        profile = await pool.fetchval(
            "SELECT id FROM subject_profiles WHERE organization_id=$1 ORDER BY created_at LIMIT 1",
            existing["org"])
        asset = await pool.fetchval(
            "SELECT id FROM media_assets WHERE storage_url=$1", "s3://fk/clip.mp4")
        return {"org": existing["org"], "loc": existing["loc"], "sys": existing["sys"],
                "disp": existing["disp"], "profile": profile, "asset": asset}
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('FkOrg','host') RETURNING id")
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('FkLoc','store') RETURNING id")
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'FkSys') RETURNING id",
        org, loc)
    disp = await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,'disp-fk','FkDisp') RETURNING id", sys)
    profile = await pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id", org)
    asset = await pool.fetchval(
        "INSERT INTO media_assets (organization_id, asset_type, storage_url, source) "
        "VALUES ($1,'video','s3://fk/clip.mp4','test') RETURNING id", org)
    return {"org": org, "loc": loc, "sys": sys, "disp": disp, "profile": profile, "asset": asset}


async def _fence(pool) -> int:
    fence = await pool.fetchval("SELECT COALESCE(max(id), 0) FROM events")
    await pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    return fence


async def _ins(pool, service, event_type, status, payload, trigger_id):
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
        trigger_id, ts, service, event_type, status, json.dumps(payload))


def _disp(extra):
    return {"screen_id": "disp-fk", "screen_kind": "display", **extra}


async def _insert_run_stream(pool, ids, tid):
    """decision -> composition -> ad_run -> playback, all sharing tid. No FK refs in
    the payloads — the projector must resolve every sibling link by trigger_id."""
    await _ins(pool, "mras-composer", "decision", "made",
               _disp({"decision_type": "identity", "decision_confidence": 0.8,
                      "target_subject_profile_id": str(ids["profile"]),
                      "decision_factors": {"why": "known"}}), tid)
    await _ins(pool, "mras-composer", "composition", "queued",
               _disp({"render_mode": "remotion", "started_at": "2026-07-01T12:02:00Z"}), tid)
    await _ins(pool, "mras-composer", "composition", "rendered",
               _disp({"render_mode": "remotion", "ended_at": "2026-07-01T12:03:00Z"}), tid)
    await _ins(pool, "mras-composer", "ad_run", "planned",
               _disp({"personalization_type": "identity",
                      "target_subject_profile_id": str(ids["profile"])}), tid)
    await _ins(pool, "mras-composer", "ad_run", "dispatched",
               _disp({"personalization_type": "identity"}), tid)
    await _ins(pool, "mras-composer", "playback", "dispatched",
               _disp({"media_asset_ref": "s3://fk/clip.mp4",
                      "dispatched_at": "2026-07-01T12:04:00Z"}), tid)
    await _ins(pool, "mras-display", "playback", "started",
               _disp({"started_at": "2026-07-01T12:04:05Z"}), tid)
    await _ins(pool, "mras-display", "playback", "ended",
               _disp({"ended_at": "2026-07-01T12:04:35Z", "duration_ms": 30000}), tid)


async def _drain(pool, dedicated_conn_factory):
    lock_conn = await dedicated_conn_factory()
    worker = ProjectorWorker(pool, lock_conn, CFG)
    assert await worker.acquire_lock() is True
    try:
        await worker.drain()
    finally:
        await worker.release_lock()


async def test_fk_links_resolved_by_shared_trigger(projector_pool, dedicated_conn_factory):
    ids = await _seed_registry(projector_pool)
    await _fence(projector_pool)
    tid = str(uuid.uuid4())
    await _insert_run_stream(projector_pool, ids, tid)

    await _drain(projector_pool, dedicated_conn_factory)

    dec = await projector_pool.fetchrow(
        "SELECT id FROM personalization_decisions WHERE trigger_id=$1", tid)
    comp = await projector_pool.fetchrow(
        "SELECT id, personalization_decision_id FROM composition_runs WHERE trigger_id=$1", tid)
    adr = await projector_pool.fetchrow(
        "SELECT id, composition_run_id, personalization_decision_id FROM ad_runs WHERE trigger_id=$1", tid)
    pb = await projector_pool.fetchrow(
        "SELECT ad_run_id, media_asset_id FROM playbacks WHERE trigger_id=$1", tid)

    # composition -> decision
    assert comp["personalization_decision_id"] == dec["id"]
    # ad_run -> composition + decision
    assert adr["composition_run_id"] == comp["id"]
    assert adr["personalization_decision_id"] == dec["id"]
    # playback -> ad_run + media_asset
    assert pb["ad_run_id"] == adr["id"]
    assert pb["media_asset_id"] == ids["asset"]


async def test_fk_link_resolution_is_idempotent(projector_pool, dedicated_conn_factory):
    ids = await _seed_registry(projector_pool)
    fence = await _fence(projector_pool)
    tid = str(uuid.uuid4())
    await _insert_run_stream(projector_pool, ids, tid)

    await _drain(projector_pool, dedicated_conn_factory)
    before = await projector_pool.fetchrow(
        "SELECT (SELECT composition_run_id FROM ad_runs WHERE trigger_id=$1) AS crid, "
        "(SELECT ad_run_id FROM playbacks WHERE trigger_id=$1) AS arid", tid)

    # rewind + refold the identical stream — links must converge, not clobber to NULL.
    await projector_pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    await _drain(projector_pool, dedicated_conn_factory)

    after = await projector_pool.fetchrow(
        "SELECT (SELECT composition_run_id FROM ad_runs WHERE trigger_id=$1) AS crid, "
        "(SELECT ad_run_id FROM playbacks WHERE trigger_id=$1) AS arid", tid)
    assert before["crid"] is not None and after["crid"] == before["crid"]
    assert before["arid"] is not None and after["arid"] == before["arid"]

    runs = await projector_pool.fetchval("SELECT count(*) FROM ad_runs WHERE trigger_id=$1", tid)
    pbs = await projector_pool.fetchval("SELECT count(*) FROM playbacks WHERE trigger_id=$1", tid)
    assert runs == 1 and pbs == 1
