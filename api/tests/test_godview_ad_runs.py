"""God View ad-runs list: server-side filter + keyset pagination + stage flags."""
import uuid

import pytest

from src.godview.ad_runs import get_ad_runs, get_ad_run_filters

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _seed_decision(pool, trig):
    """personalization_decisions.event_id is a NOT NULL FK to events — seed an events row first."""
    eid = await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1, now(), 'mras-vision','track','opened','{}'::jsonb) RETURNING id", trig)
    dec = uuid.uuid4()
    await pool.execute(
        "INSERT INTO personalization_decisions (id,trigger_id,event_id,decision_type) VALUES ($1,$2,$3,'identity')",
        dec, trig, eid)
    return dec


async def _org_loc_sys(pool, sys_name="Sys1"):
    org, loc, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await pool.execute("INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','advertiser')", org)
    await pool.execute("INSERT INTO locations (id,name,location_type) VALUES ($1,'Loc','store')", loc)
    await pool.execute("INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,$4)", sid, org, loc, sys_name)
    return org, loc, sid


async def _campaign(pool, org, name):
    cid = uuid.uuid4()
    await pool.execute("INSERT INTO campaigns (id,organization_id,name) VALUES ($1,$2,$3)", cid, org, name)
    return cid


async def test_filter_by_system(projector_pool):
    org, loc, s1 = await _org_loc_sys(projector_pool, "Sys1")
    _, _, s2 = await _org_loc_sys(projector_pool, "Sys2")
    await projector_pool.execute("INSERT INTO ad_runs (trigger_id,system_id,status) VALUES ($1,$2,'playing')", uuid.uuid4(), s1)
    await projector_pool.execute("INSERT INTO ad_runs (trigger_id,system_id,status) VALUES ($1,$2,'playing')", uuid.uuid4(), s2)
    page = await get_ad_runs(projector_pool, system_id=s1)
    assert len(page["items"]) == 1
    assert page["items"][0]["system_name"] == "Sys1"


async def test_stage_flags_reflect_pipeline(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    trig = uuid.uuid4()
    dec = await _seed_decision(projector_pool, trig)
    comp = uuid.uuid4()
    await projector_pool.execute("INSERT INTO composition_runs (id,trigger_id,status) VALUES ($1,$2,'rendered')", comp, trig)
    run = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (id,trigger_id,system_id,personalization_decision_id,composition_run_id,status) "
        "VALUES ($1,$2,$3,$4,$5,'playing')", run, trig, sid, dec, comp)
    await projector_pool.execute(
        "INSERT INTO playbacks (ad_run_id,trigger_id,screen_id,status) VALUES ($1,$2,'scr_p','ended')", run, trig)
    page = await get_ad_runs(projector_pool)
    item = next(i for i in page["items"] if str(i["id"]) == str(run))
    assert item["stage_decision"] is True
    assert item["stage_composition"] is True
    assert item["stage_playback"] is True


async def test_stage_composition_false_without_composition_run(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,system_id,status) VALUES ($1,$2,'composing')", uuid.uuid4(), sid)
    page = await get_ad_runs(projector_pool)
    assert page["items"][0]["stage_composition"] is False


async def test_keyset_pagination_no_overlap(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    for _ in range(5):
        await projector_pool.execute("INSERT INTO ad_runs (trigger_id,system_id,status) VALUES ($1,$2,'playing')", uuid.uuid4(), sid)
    p1 = await get_ad_runs(projector_pool, limit=2)
    assert len(p1["items"]) == 2
    assert p1["next_cursor"] is not None
    p2 = await get_ad_runs(projector_pool, limit=2, cursor=p1["next_cursor"])
    ids1 = {str(i["id"]) for i in p1["items"]}
    ids2 = {str(i["id"]) for i in p2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(p2["items"]) == 2


async def test_filters_list_only_referenced(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool, "SysUsed")
    cid = await _campaign(projector_pool, org, "CampUsed")
    await _campaign(projector_pool, org, "CampUnused")
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,system_id,campaign_id,status) VALUES ($1,$2,$3,'playing')", uuid.uuid4(), sid, cid)
    f = await get_ad_run_filters(projector_pool)
    assert [s["name"] for s in f["systems"]] == ["SysUsed"]
    assert [c["name"] for c in f["campaigns"]] == ["CampUsed"]
