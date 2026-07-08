"""God View dashboard: server-computed counts + bounded candidate rows."""
import uuid

import pytest

from src.godview.dashboard import get_dashboard

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _org_loc(pool):
    org, loc = uuid.uuid4(), uuid.uuid4()
    await pool.execute("INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','advertiser')", org)
    await pool.execute("INSERT INTO locations (id,name,location_type) VALUES ($1,'Loc','store')", loc)
    return org, loc


async def _system(pool, org, loc, name, status):
    sid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO systems (id,organization_id,location_id,name,status) VALUES ($1,$2,$3,$4,$5)",
        sid, org, loc, name, status)
    return sid


async def test_fleet_counts_by_status(projector_pool):
    org, loc = await _org_loc(projector_pool)
    await _system(projector_pool, org, loc, "A", "active")
    await _system(projector_pool, org, loc, "B", "active")
    await _system(projector_pool, org, loc, "C", "degraded")
    d = await get_dashboard(projector_pool)
    assert d["fleet"]["total"] == 3
    assert d["fleet"]["active"] == 2
    assert d["fleet"]["degraded"] == 1
    assert d["fleet"]["offline"] == 0
    assert d["org_count"] == 1  # one organization seeded by _org_loc


async def test_active_runs_are_bounded_and_labeled(projector_pool):
    org, loc = await _org_loc(projector_pool)
    sid = await _system(projector_pool, org, loc, "Sys1", "active")
    # one active (playing) and one completed run
    for status in ("playing", "completed"):
        await projector_pool.execute(
            "INSERT INTO ad_runs (trigger_id,system_id,status,started_at) VALUES ($1,$2,$3, now())",
            uuid.uuid4(), sid, status)
    d = await get_dashboard(projector_pool)
    assert d["active_count"] == 1
    assert len(d["active_runs"]) == 1
    assert d["active_runs"][0]["status"] == "playing"
    assert d["active_runs"][0]["system_name"] == "Sys1"


async def test_recent_failed_runs_carry_error_code(projector_pool):
    org, loc = await _org_loc(projector_pool)
    sid = await _system(projector_pool, org, loc, "Sys1", "active")
    trig = uuid.uuid4()
    comp = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO composition_runs (id,trigger_id,status,error_code) VALUES ($1,$2,'failed','OVERLAY_RENDER_TIMEOUT')",
        comp, trig)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,system_id,composition_run_id,status,ended_at) VALUES ($1,$2,$3,'failed', now())",
        trig, sid, comp)
    d = await get_dashboard(projector_pool)
    assert len(d["recent_failed_runs"]) == 1
    assert d["recent_failed_runs"][0]["error_code"] == "OVERLAY_RENDER_TIMEOUT"
    assert d["recent_failed_runs"][0]["system_name"] == "Sys1"


async def test_recent_health_drops_unify_device_and_system(projector_pool):
    org, loc = await _org_loc(projector_pool)
    sid = await _system(projector_pool, org, loc, "Sys1", "active")
    # system health drop
    await projector_pool.execute(
        "INSERT INTO system_health_events (system_id,status,detail,observed_at) "
        "VALUES ($1,'offline', '{\"message\":\"system down\"}'::jsonb, now())", sid)
    # device health drop, device projected as a camera named "CamX"
    dev = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO devices (id,system_id,device_type,name) VALUES ($1,$2,'camera','DevX')", dev, sid)
    await projector_pool.execute(
        "INSERT INTO cameras (id,system_id,device_id,name,screen_id) VALUES ($1,$2,$3,'CamX','scr_x')",
        uuid.uuid4(), sid, dev)
    await projector_pool.execute(
        "INSERT INTO device_health_events (device_id,status,detail,observed_at) "
        "VALUES ($1,'degraded', '{\"message\":\"lagging\"}'::jsonb, now())", dev)

    d = await get_dashboard(projector_pool)
    kinds = {h["kind"] for h in d["recent_health_drops"]}
    assert kinds == {"system", "device"}
    dev_row = next(h for h in d["recent_health_drops"] if h["kind"] == "device")
    assert dev_row["ref_name"] == "CamX"
    assert dev_row["detail"] == "lagging"
