"""God View systems list (counts + search + keyset) and drill-down."""
import uuid

import pytest

from src.godview.systems import get_systems, get_system

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _org_loc(pool):
    org, loc = uuid.uuid4(), uuid.uuid4()
    await pool.execute("INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Acme','advertiser')", org)
    await pool.execute("INSERT INTO locations (id,name,location_type) VALUES ($1,'Mall','mall')", loc)
    return org, loc


async def _sys(pool, org, loc, name, status="active"):
    sid = uuid.uuid4()
    await pool.execute("INSERT INTO systems (id,organization_id,location_id,name,status) VALUES ($1,$2,$3,$4,$5)",
                       sid, org, loc, name, status)
    return sid


async def test_counts_and_device_rollup(projector_pool):
    org, loc = await _org_loc(projector_pool)
    s1 = await _sys(projector_pool, org, loc, "Alpha", "active")
    await _sys(projector_pool, org, loc, "Beta", "degraded")
    await projector_pool.execute("INSERT INTO cameras (id,system_id,name,screen_id) VALUES ($1,$2,'C','scr_c1')", uuid.uuid4(), s1)
    await projector_pool.execute("INSERT INTO displays (id,system_id,screen_id) VALUES ($1,$2,'scr_d1')", uuid.uuid4(), s1)
    await projector_pool.execute("INSERT INTO unresolved_devices (screen_id,kind) VALUES ('scr_ghost','display')")

    page = await get_systems(projector_pool)
    assert page["counts"]["total_systems"] == 2
    assert page["counts"]["active_systems"] == 1
    assert page["counts"]["unresolved_devices"] == 1
    alpha = next(i for i in page["items"] if i["name"] == "Alpha")
    assert alpha["device_count"] == 2
    assert alpha["org_name"] == "Acme"
    assert alpha["location_name"] == "Mall"


async def test_search_filters_by_name(projector_pool):
    org, loc = await _org_loc(projector_pool)
    await _sys(projector_pool, org, loc, "Lobby One")
    await _sys(projector_pool, org, loc, "Bay Two")
    page = await get_systems(projector_pool, search="lobby")
    assert [i["name"] for i in page["items"]] == ["Lobby One"]


async def test_keyset_pagination_by_name(projector_pool):
    org, loc = await _org_loc(projector_pool)
    for n in ("Aaa", "Bbb", "Ccc"):
        await _sys(projector_pool, org, loc, n)
    p1 = await get_systems(projector_pool, limit=2)
    assert [i["name"] for i in p1["items"]] == ["Aaa", "Bbb"]
    assert p1["next_cursor"] is not None
    p2 = await get_systems(projector_pool, limit=2, cursor=p1["next_cursor"])
    assert [i["name"] for i in p2["items"]] == ["Ccc"]


async def test_drilldown_groups_devices(projector_pool):
    org, loc = await _org_loc(projector_pool)
    sid = await _sys(projector_pool, org, loc, "Alpha")
    grp = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO screen_groups (id,system_id,name,group_type) VALUES ($1,$2,'Wall A','ad_cluster')", grp, sid)
    await projector_pool.execute(
        "INSERT INTO cameras (id,system_id,screen_group_id,name,screen_id) VALUES ($1,$2,$3,'C1','scr_c1')", uuid.uuid4(), sid, grp)
    await projector_pool.execute(
        "INSERT INTO displays (id,system_id,screen_group_id,screen_id) VALUES ($1,$2,$3,'scr_d1')", uuid.uuid4(), sid, grp)

    d = await get_system(projector_pool, sid)
    assert d["system"]["name"] == "Alpha"
    assert len(d["screen_groups"]) == 1
    assert d["cameras"][0]["screen_group_id"] == grp
    assert "face_count" in d["cameras"][0]
    assert d["displays"][0]["screen_id"] == "scr_d1"


async def test_drilldown_missing_returns_none(projector_pool):
    assert await get_system(projector_pool, uuid.uuid4()) is None
