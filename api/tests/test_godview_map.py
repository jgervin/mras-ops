"""GET /god-view/map rollups: one set-based query, spec §3/§4 encodings.

Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import uuid

import pytest

from src.godview.map import get_map

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _org(pool):
    org = uuid.uuid4()
    await pool.execute(
        "INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','host')", org)
    return org


async def _venue(pool, name="Venue", lat=10.0, lng=20.0):
    loc = uuid.uuid4()
    await pool.execute(
        "INSERT INTO locations (id,name,location_type,city,country,lat,lng) "
        "VALUES ($1,$2,'mall','City','US',$3,$4)", loc, name, lat, lng)
    return loc


async def _system(pool, org, loc, name="Sys"):
    sid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,$4)",
        sid, org, loc, name)
    return sid


async def _camera(pool, sid, status="active"):
    await pool.execute(
        "INSERT INTO cameras (system_id,screen_id,status) VALUES ($1,$2,$3)",
        sid, f"cam-{uuid.uuid4()}", status)


async def _display(pool, sid, status="active"):
    await pool.execute(
        "INSERT INTO displays (system_id,screen_id,status) VALUES ($1,$2,$3)",
        sid, f"disp-{uuid.uuid4()}", status)


def _venue_by_name(result, name):
    return next(v for v in result["venues"] if v["name"] == name)


async def test_map_lists_only_locations_with_systems(projector_pool):
    org = await _org(projector_pool)
    loc_with = await _venue(projector_pool, "HasSystems")
    await _venue(projector_pool, "Empty")  # no systems -> not a venue
    await _system(projector_pool, org, loc_with)
    result = await get_map(projector_pool)
    names = [v["name"] for v in result["venues"]]
    assert names == ["HasSystems"]
    v = result["venues"][0]
    assert v["location_id"] == loc_with
    assert v["location_type"] == "mall"
    assert v["lat"] == 10.0 and v["lng"] == 20.0  # float8, not Decimal


async def test_map_device_counts_and_worst_status(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    s1 = await _system(projector_pool, org, loc, "S1")
    s2 = await _system(projector_pool, org, loc, "S2")
    await _camera(projector_pool, s1, "active")
    await _camera(projector_pool, s2, "degraded")
    await _display(projector_pool, s1, "active")
    await _display(projector_pool, s1, "offline")
    await _display(projector_pool, s2, "active")
    v = _venue_by_name(await get_map(projector_pool), "V")
    r = v["rollup"]
    assert r["systems"] == 2
    assert r["cameras"] == 2
    assert r["displays"] == 3
    assert r["worst_status"] == "offline"  # offline > retired > degraded > active


async def test_map_worst_status_degraded_and_all_active(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc)
    await _display(projector_pool, sid, "active")
    await _display(projector_pool, sid, "degraded")
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["worst_status"] == "degraded"


async def test_map_activity_encodings(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc)
    # composing-ish: planned ad_run
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status) VALUES ($1,$2,$3,'planned')",
        uuid.uuid4(), loc, sid)
    # composing-ish: open composition with no ad_run yet
    await projector_pool.execute(
        "INSERT INTO composition_runs (trigger_id,location_id,system_id,status) "
        "VALUES ($1,$2,$3,'rendering')", uuid.uuid4(), loc, sid)
    # playing: dispatched ad_run
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,started_at) "
        "VALUES ($1,$2,$3,'playing', now())", uuid.uuid4(), loc, sid)
    # neither: completed run (still counts toward runs_last_hour via created_at)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,ended_at) "
        "VALUES ($1,$2,$3,'completed', now())", uuid.uuid4(), loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    r = v["rollup"]
    assert r["composing_count"] == 2
    assert r["playing_count"] == 1
    assert r["active_ad_runs"] == 3
    assert r["runs_last_hour"] == 3  # the 3 ad_runs; the bare composition is not a run
    assert r["failures_last_hour"] == 0
    assert r["last_activity_at"] is not None


async def test_map_red_is_failures_last_hour_only(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc)
    # failed inside the window -> counts
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,ended_at) "
        "VALUES ($1,$2,$3,'failed', now() - interval '5 minutes')", uuid.uuid4(), loc, sid)
    # failed outside the window -> does not count (ended_at AND updated_at old)
    old_trig = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,ended_at,updated_at) "
        "VALUES ($1,$2,$3,'failed', now() - interval '2 hours', now() - interval '2 hours')",
        old_trig, loc, sid)
    # failed composition inside the window -> counts
    await projector_pool.execute(
        "INSERT INTO composition_runs (trigger_id,location_id,system_id,status,ended_at) "
        "VALUES ($1,$2,$3,'failed', now() - interval '1 minute')", uuid.uuid4(), loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["failures_last_hour"] == 2


async def test_map_idle_playback_never_glows(projector_pool):
    # Composer idle segments are ad-run-less playback/dispatched rows that never
    # end (recon finding 4) — they must NOT count as playing.
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc)
    await projector_pool.execute(
        "INSERT INTO playbacks (trigger_id,screen_id,location_id,system_id,status) "
        "VALUES ($1,'disp-idle',$2,$3,'dispatched')", uuid.uuid4(), loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["playing_count"] == 0
    # but an open playback WITH its ad_run does count
    trig = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status) "
        "VALUES ($1,$2,$3,'completed')", trig, loc, sid)
    await projector_pool.execute(
        "INSERT INTO playbacks (trigger_id,screen_id,location_id,system_id,status,started_at) "
        "VALUES ($1,'disp-live',$2,$3,'started', now())", trig, loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["playing_count"] == 1


async def test_map_null_latlng_venue_still_listed(projector_pool):
    org = await _org(projector_pool)
    loc = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO locations (id,name,location_type) VALUES ($1,'NoCoords','store')", loc)
    await _system(projector_pool, org, loc)
    v = _venue_by_name(await get_map(projector_pool), "NoCoords")
    assert v["lat"] is None and v["lng"] is None
    assert v["rollup"]["systems"] == 1


# --------------------------------------------------------------------------- #
# GET /god-view/map/locations/{id} — venue panel payload
# --------------------------------------------------------------------------- #
from src.godview.map import get_map_location  # noqa: E402  (module top in final file)


async def test_panel_nested_shape(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "Panel Venue")
    s1 = await _system(projector_pool, org, loc, "Alpha Wall")
    s2 = await _system(projector_pool, org, loc, "Beta Wall")
    await projector_pool.execute(
        "INSERT INTO cameras (system_id,screen_id,status,name,last_seen_at) "
        "VALUES ($1,'cam-a1','active','Cam A1', now())", s1)
    await projector_pool.execute(
        "INSERT INTO displays (system_id,screen_id,status,name) "
        "VALUES ($1,'disp-a1','degraded','Disp A1')", s1)
    await projector_pool.execute(
        "INSERT INTO displays (system_id,screen_id,status,name) "
        "VALUES ($1,'disp-b1','active','Disp B1')", s2)

    panel = await get_map_location(projector_pool, loc)
    assert panel["location"]["name"] == "Panel Venue"
    assert panel["location"]["lat"] == 10.0
    systems = {s["name"]: s for s in panel["systems"]}
    assert set(systems) == {"Alpha Wall", "Beta Wall"}
    alpha = systems["Alpha Wall"]
    assert [c["screen_id"] for c in alpha["cameras"]] == ["cam-a1"]
    assert alpha["cameras"][0]["last_seen_at"] is not None
    assert [d["screen_id"] for d in alpha["displays"]] == ["disp-a1"]
    assert alpha["displays"][0]["status"] == "degraded"
    assert systems["Beta Wall"]["cameras"] == []
    assert [d["screen_id"] for d in systems["Beta Wall"]["displays"]] == ["disp-b1"]


async def test_panel_ad_runs_limited_newest_first(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc, "S")
    for i in range(5):
        await projector_pool.execute(
            "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,created_at) "
            "VALUES ($1,$2,$3,'completed', now() - ($4 || ' minutes')::interval)",
            uuid.uuid4(), loc, sid, str(i))
    panel = await get_map_location(projector_pool, loc, limit=3)
    assert len(panel["ad_runs"]) == 3
    times = [r["created_at"] for r in panel["ad_runs"]]
    assert times == sorted(times, reverse=True)  # newest first
    assert panel["ad_runs"][0]["system_name"] == "S"


async def test_panel_unknown_location_returns_none(projector_pool):
    assert await get_map_location(projector_pool, uuid.uuid4()) is None


# --------------------------------------------------------------------------- #
# Globe v2 additive fields: venue org + rollup.last_run_created_at (Plan C)
# --------------------------------------------------------------------------- #
async def _named_org(pool, org_id, name):
    await pool.execute(
        "INSERT INTO organizations (id,name,organization_type) VALUES ($1,$2,'host')",
        uuid.UUID(org_id), name)
    return uuid.UUID(org_id)


async def test_map_org_is_dominant_by_system_count_with_name(projector_pool):
    a = await _named_org(projector_pool, "aaaaaaaa-0000-4000-8000-000000000001", "Alpha Retail")
    b = await _named_org(projector_pool, "bbbbbbbb-0000-4000-8000-000000000001", "Beta Retail")
    loc = await _venue(projector_pool, "V")
    await _system(projector_pool, a, loc, "S1")
    await _system(projector_pool, a, loc, "S2")
    await _system(projector_pool, b, loc, "S3")
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["org"] == {"id": a, "name": "Alpha Retail"}  # 2 systems beat 1


async def test_map_org_tiebreak_is_count_desc_then_org_id(projector_pool):
    # equal counts -> lower organization_id wins (deterministic, testable)
    lo = await _named_org(projector_pool, "10000000-0000-4000-8000-000000000001", "Low Org")
    hi = await _named_org(projector_pool, "20000000-0000-4000-8000-000000000001", "High Org")
    loc = await _venue(projector_pool, "V")
    await _system(projector_pool, hi, loc, "S-hi")
    await _system(projector_pool, lo, loc, "S-lo")
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["org"] == {"id": lo, "name": "Low Org"}


async def test_map_last_run_created_at_none_without_runs(projector_pool):
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    await _system(projector_pool, org, loc)
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["last_run_created_at"] is None
    assert v["org"] is not None  # org present even with zero activity


async def test_map_last_run_created_at_is_unwindowed_max(projector_pool):
    # NOT windowed like runs_last_hour: a 2h-old run still counts; the max is
    # monotone (Lane 3's "new run" delta signal must never move backwards).
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status,created_at) "
        "VALUES ($1,$2,$3,'completed', now() - interval '2 hours')",
        uuid.uuid4(), loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    first = v["rollup"]["last_run_created_at"]
    assert first is not None
    assert v["rollup"]["runs_last_hour"] == 0  # windowed sibling disagrees — by design
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status) "
        "VALUES ($1,$2,$3,'completed')", uuid.uuid4(), loc, sid)
    v = _venue_by_name(await get_map(projector_pool), "V")
    assert v["rollup"]["last_run_created_at"] > first  # advanced


async def test_panel_ad_runs_carry_display_id(projector_pool):
    # Lane 3's traveling pulse needs the display end of each run. display_id is
    # projector-populated from display-scope events; camera-scope-only runs
    # (e.g. generator failure path) legitimately keep it NULL.
    org = await _org(projector_pool)
    loc = await _venue(projector_pool, "V")
    sid = await _system(projector_pool, org, loc, "S")
    disp = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO displays (id,system_id,screen_id) VALUES ($1,$2,'disp-x')",
        disp, sid)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,display_id,status) "
        "VALUES ($1,$2,$3,$4,'completed')", uuid.uuid4(), loc, sid, disp)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,location_id,system_id,status) "
        "VALUES ($1,$2,$3,'failed')", uuid.uuid4(), loc, sid)
    panel = await get_map_location(projector_pool, loc)
    by_status = {r["status"]: r for r in panel["ad_runs"]}
    assert by_status["completed"]["display_id"] == disp
    assert by_status["failed"]["display_id"] is None  # nullable, key always present
