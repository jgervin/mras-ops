"""God View ad-runs list: server-side filter + keyset pagination + stage flags."""
import uuid

import pytest

from src.godview.ad_runs import get_ad_runs, get_ad_run_filters

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _seed_decision(pool, trig, target_subject_profile_id=None):
    """personalization_decisions.event_id is a NOT NULL FK to events — seed an events row first."""
    eid = await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1, now(), 'mras-vision','track','opened','{}'::jsonb) RETURNING id", trig)
    dec = uuid.uuid4()
    await pool.execute(
        "INSERT INTO personalization_decisions (id,trigger_id,event_id,decision_type,target_subject_profile_id) "
        "VALUES ($1,$2,$3,'identity',$4)",
        dec, trig, eid, target_subject_profile_id)
    return dec


async def _creative_refs(pool, org):
    """component_id/ad_id/input_asset_id/output_asset_id are real FKs — seed referenced rows."""
    comp = await pool.fetchval(
        "INSERT INTO components (name, slug) VALUES ('Comp', $1) RETURNING id", f"comp-{uuid.uuid4()}")
    ad = await pool.fetchval(
        "INSERT INTO ads (name, base_video, component_id) VALUES ('Ad', 'video.mp4', $1) RETURNING id", comp)
    asset_in = await pool.fetchval(
        "INSERT INTO media_assets (organization_id, asset_type, storage_url, source) "
        "VALUES ($1, 'image', 's3://in.png', 'test') RETURNING id", org)
    asset_out = await pool.fetchval(
        "INSERT INTO media_assets (organization_id, asset_type, storage_url, source) "
        "VALUES ($1, 'video', 's3://out.mp4', 'test') RETURNING id", org)
    return ad, comp, asset_in, asset_out


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


from src.godview.ad_runs import get_ad_run


async def test_ad_run_detail_bundles_pipeline(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    trig = uuid.uuid4()
    profile = await projector_pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id", org)
    dec = await _seed_decision(projector_pool, trig, target_subject_profile_id=profile)  # decision_type='identity'
    ad_id, component_id, asset_in, asset_out = await _creative_refs(projector_pool, org)
    comp = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO composition_runs (id,trigger_id,render_mode,status,error_code,"
        "ad_id,component_id,input_asset_id,output_asset_id,used_spoken_name,used_visible_name) "
        "VALUES ($1,$2,'template_overlay','failed','OVERLAY_RENDER_TIMEOUT',$3,$4,$5,$6,true,false)",
        comp, trig, ad_id, component_id, asset_in, asset_out)
    run = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (id,trigger_id,system_id,personalization_decision_id,composition_run_id,status) "
        "VALUES ($1,$2,$3,$4,$5,'failed')", run, trig, sid, dec, comp)
    await projector_pool.execute(
        "INSERT INTO playbacks (ad_run_id,trigger_id,screen_id,status) VALUES ($1,$2,'scr_p','failed')", run, trig)

    d = await get_ad_run(projector_pool, run)
    assert str(d["ad_run"]["id"]) == str(run)
    assert d["personalization_decision"]["decision_type"] == "identity"
    assert d["composition_run"]["error_code"] == "OVERLAY_RENDER_TIMEOUT"
    assert len(d["playbacks"]) == 1
    assert str(d["personalization_decision"]["target_subject_profile_id"]) == str(profile)
    assert str(d["composition_run"]["ad_id"]) == str(ad_id)
    assert str(d["composition_run"]["component_id"]) == str(component_id)
    assert str(d["composition_run"]["input_asset_id"]) == str(asset_in)
    assert str(d["composition_run"]["output_asset_id"]) == str(asset_out)
    assert d["composition_run"]["used_spoken_name"] is True
    assert d["composition_run"]["used_visible_name"] is False


async def test_ad_run_detail_missing_returns_none(projector_pool):
    assert await get_ad_run(projector_pool, uuid.uuid4()) is None


async def _seed_exposure_rows(pool, run, sid, org):
    """viewer_exposures.subject_observation_id is NOT NULL (018) — seed observations.
    Grain: 2 known rows share ONE profile (identified dedupes), 2 anonymous rows."""
    profile = await pool.fetchval(
        "INSERT INTO subject_profiles (organization_id, status) VALUES ($1,'known') RETURNING id", org)

    async def obs():
        return await pool.fetchval(
            "INSERT INTO subject_observations (system_id, observed_at, detection_type, match_status) "
            "VALUES ($1, now(), 'face', 'no_match') RETURNING id", sid)

    o1, o2, o3, o4 = await obs(), await obs(), await obs(), await obs()
    await pool.execute(
        "INSERT INTO viewer_exposures (ad_run_id, subject_observation_id, subject_profile_id, "
        "role, identity_status, watched, watch_probability, attending_fraction, gaze_duration_ms) VALUES "
        "($1,$2,$5,'target','known',true,NULL,0.8,4000), "
        "($1,$3,$5,'bystander','known',NULL,0.4,0.4,NULL), "
        "($1,$4,NULL,'bystander','anonymous',NULL,0.5,0.5,1000), "
        "($1,$6,NULL,'bystander','unmatched',NULL,0.9,0.9,1000)",
        run, o1, o2, o3, profile, o4)


async def test_ad_run_detail_includes_viewer_exposure_summary(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    run = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (id,trigger_id,system_id,status) VALUES ($1,$2,$3,'completed')",
        run, uuid.uuid4(), sid)
    await _seed_exposure_rows(projector_pool, run, sid, org)
    d = await get_ad_run(projector_pool, run)
    exp = d["viewer_exposure"]
    assert exp["exposure_rows"] == 4
    assert exp["identified_viewers"] == 1                # 2 known rows, 1 distinct profile
    assert exp["anonymous_observations"] == 2             # non-'known' rows (row grain)
    assert exp["estimated_viewers"] == 3
    assert exp["target_rows"] == 1
    assert exp["target_watched"] is True
    assert exp["avg_watch_probability"] == pytest.approx(0.6)   # (0.4+0.5+0.9)/3
    assert exp["avg_attending_fraction"] == pytest.approx(0.65) # (0.8+0.4+0.5+0.9)/4
    assert exp["total_gaze_ms"] == 6000
    # ad_runs rollup columns surfaced as-is (all NULL today — no producer, see plan)
    assert d["ad_run"]["estimated_total_viewers"] is None
    assert d["ad_run"]["target_watched"] is None


async def test_ad_run_detail_viewer_exposure_empty(projector_pool):
    org, loc, sid = await _org_loc_sys(projector_pool)
    run = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (id,trigger_id,system_id,status) VALUES ($1,$2,$3,'completed')",
        run, uuid.uuid4(), sid)
    d = await get_ad_run(projector_pool, run)
    assert d["viewer_exposure"]["exposure_rows"] == 0
    assert d["viewer_exposure"]["estimated_viewers"] == 0
    assert d["viewer_exposure"]["target_watched"] is None
